"""Trusted Gemini Flash chat coordinator. Sandboxes receive execution tasks only."""

import copy
import time

import logfire
from opentelemetry import context, propagate
from sqlalchemy import select

from app.agent_models import Call, Message, Run, Workspace, uid
from app.agent_runtime.contracts import ChatDecision
from app.services.agent.chat import ChatService
from app.services.agent.db import transaction
from app.services.agent.provider import GeminiProvider
from app.services.agent.service import digest, emit


class ChatCoordinator:
    def __init__(self, service, provider=None):
        self.service, self.provider = service, provider
        self.chat = ChatService(service)

    def tick(self):
        svc = self.service
        claim = None
        with transaction(svc.engine) as session:
            session.scalar(
                select(Workspace)
                .where(Workspace.id == svc.settings.workspace)
                .with_for_update()
            )
            rows = session.scalars(
                select(Run)
                .where(
                    Run.workspace == svc.settings.workspace,
                    Run.status.in_(
                        [
                            "chat_queued",
                            "chat_preparing",
                            "chat_returning",
                            "execution_done",
                            "failed",
                            "timed_out",
                            "cancelled",
                        ]
                    ),
                    ~select(Message.id)
                    .where(Message.run_id == Run.id, Message.role == "assistant")
                    .exists(),
                )
                .order_by(Run.created_at)
            ).all()
            for run in rows:
                if not run.request.get("conversation_id"):
                    continue
                replied = session.scalar(
                    select(Message.id).where(
                        Message.run_id == run.id, Message.role == "assistant"
                    )
                )
                if replied:
                    continue
                if run.status in {"chat_preparing", "chat_returning"}:
                    if run.lease_until > time.time():
                        return
                    phase = "prepare" if run.status == "chat_preparing" else "result"
                    saved = session.scalar(
                        select(Call).where(
                            Call.run_id == run.id, Call.step_id == "chat_" + phase
                        )
                    )
                    if saved and saved.status == "completed":
                        self.apply(
                            session,
                            run,
                            phase,
                            ChatDecision.model_validate(saved.response),
                        )
                    else:
                        self.failure(session, run, phase, "chat_outcome_unknown")
                    return
                if run.status in {"execution_done", "failed", "timed_out", "cancelled"}:
                    if run.status == "cancelled":
                        self.publish(
                            session,
                            run,
                            "Cancelled. Send a new message when you want to continue.",
                            "cancelled",
                        )
                        return
                    run.result = {
                        **run.result,
                        "execution_status": run.result.get(
                            "execution_status", run.status
                        ),
                    }
                    claim = (run, "result")
                    break
                if run.status == "chat_queued":
                    claim = (run, "prepare")
                    break
            if claim:
                run, phase = claim
                run.status = (
                    "chat_preparing" if phase == "prepare" else "chat_returning"
                )
                run.stage = run.status
                run.lease_until = time.time() + 180
                if not run.traceparent:
                    carrier = {}
                    propagate.inject(carrier)
                    run.traceparent = carrier.get("traceparent")
                    run.trace_id = (
                        run.traceparent.split("-")[1] if run.traceparent else None
                    )
                row = self.chat.conversation(session, run.request["conversation_id"])
                model_context = {
                    "phase": phase,
                    "messages": self.chat.history(session, row),
                    "subject_asset_id": row.subject_asset_id,
                    "assets": [
                        {
                            **svc.asset_view(svc.asset(session, i)),
                            "source": svc.asset(session, i).source,
                        }
                        for i in row.asset_ids
                    ],
                }
                if phase == "result":
                    model_context["result"] = {
                        **run.result,
                        "error_code": run.error_code,
                        "evaluations": run.checkpoint.get("evaluations", {}),
                    }
                brand = svc.brand(session, row.brand_version).profile
                step = "chat_" + phase
                session.add(
                    Call(
                        run_id=run.id,
                        step_id=step,
                        operation=step,
                        request_hash=digest({"context": model_context, "brand": brand}),
                    )
                )
                emit(
                    session,
                    run,
                    run.stage,
                    "Preparing the execution request."
                    if phase == "prepare"
                    else "Preparing the chat response.",
                )
                run_id = run.id
                parent = run.traceparent
        if not claim:
            return
        token = (
            context.attach(propagate.extract({"traceparent": parent}))
            if parent
            else None
        )
        owned = self.provider is None
        provider = self.provider
        try:
            with logfire.span("chat." + phase, run_id=run_id):
                try:
                    provider = provider or GeminiProvider(svc.settings)
                    raw, usage = provider.chat(model_context, brand)
                    decision = ChatDecision.model_validate(raw)
                except Exception:  # noqa: BLE001 -- never replay a possibly billed request or expose raw provider errors
                    with transaction(svc.engine) as session:
                        run = svc.run(session, run_id, True)
                        call = session.scalar(
                            select(Call).where(
                                Call.run_id == run_id, Call.step_id == step
                            )
                        )
                        call.status, call.error_code = "failed", "chat_model_failed"
                        if run.status in {"chat_preparing", "chat_returning"}:
                            self.failure(session, run, phase, "chat_model_failed")
                    return
            with transaction(svc.engine) as session:
                run = svc.run(session, run_id, True)
                call = session.scalar(
                    select(Call).where(Call.run_id == run_id, Call.step_id == step)
                )
                call.status, call.response, call.usage = (
                    "completed",
                    decision.model_dump(),
                    usage,
                )
                expected = "chat_preparing" if phase == "prepare" else "chat_returning"
                if run.status == expected:
                    self.apply(session, run, phase, decision)
        finally:
            if owned and provider is not None:
                provider.close()
            if token is not None:
                context.detach(token)

    def apply(self, session, run, phase, decision):
        svc = self.service
        if phase == "result":
            if decision.action != "reply":
                self.failure(session, run, phase, "invalid_chat_result")
                return
            self.publish(
                session,
                run,
                decision.message,
                run.result.get("execution_status", "failed"),
            )
            return
        if decision.action == "reply":
            self.publish(session, run, decision.message, "succeeded")
            return
        if svc.settings.blockers():
            run.error_code = "execution_not_configured"
            self.publish(
                session,
                run,
                "The image executor is not configured yet. Configure its public HTTPS gateway before generating.",
                "failed",
            )
            return
        request = decision.execution
        row = self.chat.conversation(session, run.request["conversation_id"], True)
        asset_id = request.asset_id or (
            row.subject_asset_id if not request.image_id else None
        )
        if asset_id and asset_id not in row.asset_ids:
            run.error_code = "invalid_reference"
            self.publish(
                session,
                run,
                "Please choose an image attached to this conversation.",
                "failed",
            )
            return
        overrides = request.overrides.model_dump(exclude_none=True)
        effective = copy.deepcopy(svc.brand(session, row.brand_version).profile)
        effective.update(overrides)
        task = {
            **request.model_dump(),
            "asset_id": asset_id,
            "brand": effective,
            "brand_version": row.brand_version,
            "user_request": run.request["prompt"],
            "priority": "current user request > conversation context > saved brand defaults",
        }
        run.request = {
            **run.request,
            "mode": "execution",
            "execution": task,
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "subject_asset_ids": [asset_id] if asset_id else [],
        }
        run.status, run.stage = "queued", "queued"
        emit(
            session,
            run,
            "execution_queued",
            "The compiled design task is ready for the sandbox.",
        )

    def failure(self, session, run, phase, code):
        if phase == "result":
            status = run.result.get("execution_status", "failed")
            message = (
                "Your image is ready. The chat summary is unavailable; inspect the attached result."
                if status == "succeeded"
                else "The image task could not finish. Please check the image ID and try a new message."
            )
            if run.result.get("asset_ids") and status != "succeeded":
                message = "Your images were generated and saved, but review could not finish. Inspect the attached images before using them; you do not need to generate them again."
            run.result = {**run.result, "chat_error_code": code}
            self.publish(session, run, message, status)
        else:
            run.error_code = code
            self.publish(
                session,
                run,
                "I couldn't process this message safely. Please send a new message to try again.",
                "failed",
            )

    def publish(self, session, run, text, status):
        row = self.chat.conversation(session, run.request["conversation_id"], True)
        if session.scalar(
            select(Message.id).where(
                Message.run_id == run.id, Message.role == "assistant"
            )
        ):
            return
        outputs = run.result.get("asset_ids", [])
        if outputs:
            row.asset_ids = list(
                dict.fromkeys(
                    [*row.asset_ids, *run.checkpoint.get("generated", []), *outputs]
                )
            )
            if status == "succeeded":
                row.subject_asset_id = outputs[-1]
        row.sequence += 1
        row.updated_at = time.time()
        message = Message(
            id=uid(),
            conversation_id=row.id,
            sequence=row.sequence,
            role="assistant",
            content=text,
            run_id=run.id,
            asset_ids=outputs,
        )
        session.add(message)
        run.result = {**run.result, "message_id": message.id, "message": text}
        run.status, run.stage = status, "completed" if status == "succeeded" else status
        emit(
            session,
            run,
            "assistant_message",
            "The chat service replied to the conversation.",
        )
