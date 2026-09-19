"""Durable chat API. Conversation planning happens in the trusted coordinator."""

import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_models import Conversation, Message, Run, Workspace, uid
from app.agent_runtime.contracts import RunInput
from app.services.agent.db import transaction
from app.services.agent.service import TERMINAL, digest
from app.services.agent.storage import AgentError


class ChatService:
    def __init__(self, service):
        self.service = service

    def conversation(self, session, conversation_id, lock=False):
        query = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace == self.service.settings.workspace,
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise AgentError("not_found", "Conversation not found.", 404)
        return row

    @staticmethod
    def view(row):
        return {
            "id": row.id,
            "title": row.title,
            "brand_version": row.brand_version,
            "subject_asset_id": row.subject_asset_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def create(self, body):
        with transaction(self.service.engine) as session:
            self.service.brand(session, body.brand_version)
            row = Conversation(
                id=uid(),
                workspace=self.service.settings.workspace,
                title=body.title,
                brand_version=body.brand_version,
            )
            session.add(row)
            session.flush()
            return self.view(row)

    def list(self):
        with Session(self.service.engine) as session:
            return [
                self.view(row)
                for row in session.scalars(
                    select(Conversation)
                    .where(Conversation.workspace == self.service.settings.workspace)
                    .order_by(Conversation.updated_at.desc())
                    .limit(100)
                )
            ]

    def history(self, session, row):
        result = []
        for message in session.scalars(
            select(Message)
            .where(Message.conversation_id == row.id)
            .order_by(Message.sequence)
        ):
            run = self.service.run(session, message.run_id)
            result.append(
                {
                    "id": message.id,
                    "sequence": message.sequence,
                    "role": message.role,
                    "content": message.content,
                    "asset_ids": message.asset_ids,
                    "run_id": run.id,
                    "run_status": run.status,
                    "error_code": run.error_code,
                    "created_at": message.created_at,
                }
            )
        return result

    def read(self, conversation_id):
        with Session(self.service.engine) as session:
            row = self.conversation(session, conversation_id)
            return {
                **self.view(row),
                "messages": self.history(session, row),
                "assets": [
                    {
                        **self.service.asset_view(self.service.asset(session, i)),
                        "source": self.service.asset(session, i).source,
                    }
                    for i in row.asset_ids
                ],
            }

    def submit(self, conversation_id, body, key):
        svc = self.service
        fingerprint = digest(body.model_dump())
        with transaction(svc.engine) as session:
            session.scalar(
                select(Workspace)
                .where(Workspace.id == svc.settings.workspace)
                .with_for_update()
            )
            row = self.conversation(session, conversation_id, True)
            existing = session.scalar(
                select(Message).where(
                    Message.conversation_id == row.id, Message.idempotency_key == key
                )
            )
            if existing:
                if existing.request_hash != fingerprint:
                    raise AgentError(
                        "idempotency_conflict",
                        "This message key has different content.",
                    )
                return {
                    "message_id": existing.id,
                    "run": svc.run_view(session, svc.run(session, existing.run_id)),
                }
            busy = session.scalar(
                select(Run.id)
                .join(Message, Message.run_id == Run.id)
                .where(Message.conversation_id == row.id, Run.status.not_in(TERMINAL))
                .limit(1)
            )
            if busy:
                raise AgentError(
                    "conversation_busy",
                    "Wait for this conversation's current turn or cancel it.",
                )
            count = session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == row.id, Message.role == "user")
            )
            if count >= 20:
                raise AgentError(
                    "conversation_limit",
                    "This conversation reached 20 turns. Start a new conversation.",
                )
            if body.subject_asset_id:
                svc.asset(session, body.subject_asset_id)
                if body.subject_asset_id not in row.asset_ids:
                    row.asset_ids = [*row.asset_ids, body.subject_asset_id]
                row.subject_asset_id = body.subject_asset_id
            request = RunInput(
                brand_version=row.brand_version,
                prompt=body.content,
                candidate_count=1,
                subject_asset_ids=[row.subject_asset_id]
                if row.subject_asset_id
                else [],
            )
            view = svc.create_run(
                request,
                "chat:" + row.id + ":" + digest(key)[:32],
                db_session=session,
                require_execution=False,
            )
            run = svc.run(session, view["id"])
            run.request = {**run.request, "mode": "chat", "conversation_id": row.id}
            run.status, run.stage = "chat_queued", "chat_queued"
            row.sequence += 1
            row.updated_at = time.time()
            message = Message(
                id=uid(),
                conversation_id=row.id,
                sequence=row.sequence,
                role="user",
                content=body.content,
                run_id=run.id,
                asset_ids=[body.subject_asset_id] if body.subject_asset_id else [],
                idempotency_key=key,
                request_hash=fingerprint,
            )
            session.add(message)
            session.flush()
            return {"message_id": message.id, "run": svc.run_view(session, run)}
