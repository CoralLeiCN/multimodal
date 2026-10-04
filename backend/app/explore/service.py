"""Durable, user-owned runs. One API supervisor owns its Codex child processes."""

import asyncio
import hashlib
import json
import os
import secrets
import signal
import sys
import time
from contextlib import suppress

import logfire
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import ROOT
from app.explore import tools
from app.explore.contracts import Answer
from app.explore.db import migrate
from app.explore.models import Conversation, Event, Run, Upload, uid
from app.services.selection import validate_image

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def event(session, run, kind, data):
    session.add(Event(run_id=run.id, kind=kind, data=data))


class Explorer:
    def __init__(self, search, settings, runner=None):
        self.search, self.engine, self.settings = search, search.engine, settings
        self.runner = runner or self.execute
        self.tasks = {}
        self.slots = asyncio.Semaphore(settings.max_concurrent)
        self.lock_connection = None
        self.lock_engine = None
        self.lock_watch = None
        self.ownership_lost = False

    def start(self):
        # A second API must not recover or replay work owned by a live supervisor.
        if self.engine.dialect.name == "postgresql":
            self.lock_engine = create_engine(
                self.search.settings.migration_database_url,
                poolclass=NullPool,
                hide_parameters=True,
                connect_args={"connect_timeout": 15},
            )
            self.lock_connection = self.lock_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            )
            if not self.lock_connection.scalar(
                text(
                    "SELECT pg_try_advisory_lock(hashtext(current_database() || current_schema() || 'explorer-supervisor'))"
                )
            ):
                self.release_lock()
                raise RuntimeError(
                    "Only one explorer API supervisor may use this database schema."
                )
        try:
            migrate(self.lock_engine or self.engine)
            self.settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            with Session(self.engine) as session, session.begin():
                for run in session.scalars(
                    select(Run).where(Run.status.not_in(TERMINAL))
                ):
                    self.finish(session, run, "failed", error="interrupted")
                for conversation in session.scalars(
                    select(Conversation).where(Conversation.active_run.is_not(None))
                ):
                    conversation.active_run = None
        except BaseException:
            self.release_lock()
            raise

    async def watch_lock(self):
        while True:
            await asyncio.sleep(5)
            try:
                if self.lock_connection is not None:
                    self.lock_connection.scalar(text("SELECT 1"))
            except SQLAlchemyError:
                self.ownership_lost = True
                for task in list(self.tasks.values()):
                    task.cancel()
                return

    def release_lock(self):
        if self.lock_connection is not None:
            try:
                if not self.lock_connection.invalidated:
                    self.lock_connection.execute(
                        text("SELECT pg_advisory_unlock_all()")
                    )
            finally:
                self.lock_connection.close()
                self.lock_connection = None
                if self.lock_engine is not None:
                    self.lock_engine.dispose()
                    self.lock_engine = None

    async def close(self):
        if self.lock_watch:
            self.lock_watch.cancel()
            await asyncio.gather(self.lock_watch, return_exceptions=True)
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
        self.release_lock()

    def conversation(self, session, owner, conversation_id, *, lock=False):
        query = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.owner == owner
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise HTTPException(404, "Conversation not found.")
        return row

    def create(self, owner, title):
        with Session(self.engine) as session, session.begin():
            if (
                session.scalar(
                    select(func.count())
                    .select_from(Conversation)
                    .where(Conversation.owner == owner)
                )
                >= 100
            ):
                raise HTTPException(409, "Conversation storage limit reached.")
            row = Conversation(owner=owner, title=title)
            session.add(row)
            session.flush()
            return {"id": row.id, "title": row.title}

    def list(self, owner):
        with Session(self.engine) as session:
            return [
                {"id": c.id, "title": c.title}
                for c in session.scalars(
                    select(Conversation)
                    .where(Conversation.owner == owner)
                    .order_by(Conversation.created_at.desc())
                    .limit(100)
                )
            ]

    @staticmethod
    def view(run):
        return {
            "id": run.id,
            "content": run.content,
            "upload_id": run.upload_id,
            "status": run.status,
            "answer": run.answer,
            "results": run.results,
            "error": run.error,
            "usage": run.usage,
        }

    def history(self, owner, conversation_id):
        with Session(self.engine) as session:
            row = self.conversation(session, owner, conversation_id)
            runs = session.scalars(
                select(Run)
                .where(Run.conversation_id == row.id)
                .order_by(Run.created_at, Run.id)
            )
            return {
                "id": row.id,
                "title": row.title,
                "runs": [self.view(r) for r in runs],
            }

    def submit(self, owner, conversation_id, body, key):
        if not 1 <= len(key) <= 100:
            raise HTTPException(422, "A bounded Idempotency-Key is required.")
        if self.ownership_lost:
            raise HTTPException(503, "The conversation service requires a restart.")
        fingerprint = digest(body.model_dump_json())
        with Session(self.engine) as session, session.begin():
            row = self.conversation(session, owner, conversation_id, lock=True)
            existing = session.scalar(
                select(Run).where(Run.conversation_id == row.id, Run.request_key == key)
            )
            if existing:
                if existing.fingerprint != fingerprint:
                    raise HTTPException(
                        409, "Idempotency key already used with different content."
                    )
                return self.view(existing)
            if not self.settings.ready:
                raise HTTPException(
                    503, "Configure the Codex API key and model to enable exploration."
                )
            if row.active_run:
                raise HTTPException(
                    409, "A turn is already active in this conversation."
                )
            if (
                session.scalar(
                    select(func.count())
                    .select_from(Run)
                    .where(Run.conversation_id == row.id)
                )
                >= self.settings.max_turns
            ):
                raise HTTPException(
                    409, "Start a new conversation to continue exploring."
                )
            if (
                session.scalar(
                    select(func.count())
                    .select_from(Run)
                    .where(Run.status.not_in(TERMINAL))
                )
                >= self.settings.max_concurrent * 4
            ):
                raise HTTPException(
                    429, "The collection companion is busy. Try again shortly."
                )
            if body.upload_id:
                self.upload(session, owner, body.upload_id)
            run = Run(
                conversation_id=row.id,
                request_key=key,
                fingerprint=fingerprint,
                content=body.content,
                upload_id=body.upload_id,
            )
            session.add(run)
            session.flush()
            row.active_run = run.id
            event(session, run, "status", {"status": "queued"})
            result = self.view(run)
        run_id = result["id"]
        task = asyncio.create_task(self.perform(run_id))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _: self.release_run(run_id))
        return result

    def release_run(self, run_id):
        self.tasks.pop(run_id, None)
        with Session(self.engine) as session, session.begin():
            run = session.get(Run, run_id)
            if run is not None:
                row = session.get(
                    Conversation, run.conversation_id, with_for_update=True
                )
                if row.active_run == run_id:
                    row.active_run = None

    def finish(self, session, run, status, *, error=None):
        run.status, run.error, run.token_hash = status, error, None
        event(session, run, "status", {"status": status, "error": error})

    async def perform(self, run_id):
        try:
            async with self.slots:
                token = secrets.token_urlsafe(32)
                with Session(self.engine) as session, session.begin():
                    run = session.get(Run, run_id, with_for_update=True)
                    if run.status in TERMINAL:
                        return
                    row = session.get(Conversation, run.conversation_id)
                    run.status, run.token_hash = "running", digest(token)
                    run.deadline = time.time() + self.settings.run_timeout
                    event(session, run, "status", {"status": "running"})
                    payload = {
                        "run_id": run.id,
                        "conversation_id": row.id,
                        "thread_id": row.thread_id,
                        "content": run.content,
                        "upload_id": run.upload_id,
                        "model": self.settings.model,
                        "tool_url": f"{self.settings.gateway_url}/api/v1/explorer/internal/{run.id}/tool",
                        "tool_token": token,
                        "api_key": self.settings.api_key.get_secret_value(),
                        "timeout": self.settings.run_timeout,
                        "parent_pid": os.getpid(),
                    }
                    if run.upload_id:
                        data, _ = self.read_upload(row.owner, run.upload_id)
                        payload["image"] = tools.preview(data)
                with logfire.span(
                    "explorer.run", run_id=run_id, model=self.settings.model
                ):
                    async with asyncio.timeout(self.settings.run_timeout):
                        await self.runner(
                            payload, lambda data: self.accept(run_id, data)
                        )
                with Session(self.engine) as session, session.begin():
                    run = session.get(Run, run_id, with_for_update=True)
                    if run.status not in TERMINAL:
                        self.finish(session, run, "failed", error="incomplete_response")
        except asyncio.CancelledError:
            self.fail(run_id, "cancelled", "cancelled")
            raise
        except TimeoutError:
            self.fail(run_id, "timed_out", "timed_out")
        except Exception:  # noqa: BLE001 -- sanitize the provider boundary
            # SDK errors can contain prompts, credentials and server stderr.
            self.fail(run_id, "failed", "codex_unavailable")

    def fail(self, run_id, status, error):
        with Session(self.engine) as session, session.begin():
            run = session.get(Run, run_id, with_for_update=True)
            if run and run.status not in TERMINAL:
                self.finish(session, run, status, error=error)

    def accept(self, run_id, data):
        with Session(self.engine) as session, session.begin():
            run = session.get(Run, run_id, with_for_update=True)
            if run.status != "running":
                return
            kind = data.get("kind")
            if kind == "thread":
                value = data.get("id")
                if not isinstance(value, str) or not 1 <= len(value) <= 100:
                    raise ValueError("Invalid thread")
                session.get(Conversation, run.conversation_id).thread_id = value
            elif kind == "progress":
                tool = data.get("tool")
                if tool in tools.TOOL_NAMES:
                    event(session, run, "tool", {"name": tool})
            elif kind == "result":
                if (
                    run.index_version
                    and self.search.generation().id != run.index_version
                ):
                    self.finish(session, run, "failed", error="index_changed")
                    return
                answer = Answer.model_validate(data["result"])
                ids = list(dict.fromkeys(answer.image_ids))
                if any(i not in run.evidence for i in ids):
                    self.finish(
                        session, run, "failed", error="invalid_result_reference"
                    )
                    return
                run.answer = answer.answer
                run.results = [run.evidence[i] for i in ids]
                usage = data.get("usage") or {}
                run.usage = {
                    k: v
                    for k, v in usage.items()
                    if k in {"input_tokens", "output_tokens", "cached_input_tokens"}
                    and type(v) is int
                    and v >= 0
                }
                event(
                    session,
                    run,
                    "answer",
                    {"answer": run.answer, "results": run.results},
                )
                self.finish(session, run, "succeeded")

    def authorize_run(self, session, owner, run_id, *, lock=False):
        query = (
            select(Run)
            .join(Conversation)
            .where(Run.id == run_id, Conversation.owner == owner)
        )
        if lock:
            query = query.with_for_update(of=Run)
        run = session.scalar(query)
        if not run:
            raise HTTPException(404, "Run not found.")
        return run

    def cancel(self, owner, run_id):
        with Session(self.engine) as session, session.begin():
            run = self.authorize_run(session, owner, run_id, lock=True)
            if run.status not in TERMINAL:
                self.finish(session, run, "cancelled", error="cancelled")
        if task := self.tasks.get(run_id):
            task.cancel()
        return {"status": "cancelled"}

    def events(self, owner, run_id, after):
        with Session(self.engine) as session:
            run = self.authorize_run(session, owner, run_id)
            records = session.scalars(
                select(Event)
                .where(Event.run_id == run.id, Event.id > after)
                .order_by(Event.id)
                .limit(100)
            )
            return [
                {"id": e.id, "kind": e.kind, "data": e.data} for e in records
            ], run.status in TERMINAL

    @staticmethod
    def upload(session, owner, upload_id):
        row = session.scalar(
            select(Upload).where(Upload.id == upload_id, Upload.owner == owner)
        )
        if not row:
            raise HTTPException(404, "Upload not found.")
        return row

    def save_upload(self, owner, data):
        mime, _, _ = validate_image(data, self.search.settings)
        with Session(self.engine) as session, session.begin():
            if (
                session.scalar(
                    select(func.count())
                    .select_from(Upload)
                    .where(Upload.owner == owner)
                )
                >= 100
            ):
                raise HTTPException(409, "Upload storage limit reached.")
            row = Upload(
                id=uid(),
                owner=owner,
                mime=mime,
                checksum=hashlib.sha256(data).hexdigest(),
            )
            folder = self.settings.state_dir / "uploads"
            folder.mkdir(mode=0o700, exist_ok=True)
            path = folder / row.id
            try:
                with path.open("xb") as file:
                    file.write(data)
                session.add(row)
                session.flush()
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return {"id": row.id}

    def read_upload(self, owner, upload_id):
        with Session(self.engine) as session:
            row = self.upload(session, owner, upload_id)
            with (self.settings.state_dir / "uploads" / row.id).open("rb") as source:
                data = source.read(self.search.settings.max_image_bytes + 1)
            if hashlib.sha256(data).hexdigest() != row.checksum:
                raise HTTPException(409, "Upload is unavailable.")
            return data, row.mime

    def tool(self, run_id, token, body):
        def authorized(session):
            run = session.get(Run, run_id, with_for_update=True)
            if (
                self.ownership_lost
                or not run
                or run.status != "running"
                or run.deadline is None
                or run.deadline <= time.time()
                or not run.token_hash
                or not secrets.compare_digest(run.token_hash, digest(token))
            ):
                raise HTTPException(403, "Tool access expired.")
            return run

        with Session(self.engine) as session, session.begin():
            run = authorized(session)
            if run.tool_calls >= self.settings.max_tool_calls:
                raise HTTPException(
                    429, "Tool call budget reached; finish with the collected evidence."
                )
            run.tool_calls += 1
            owner = session.get(Conversation, run.conversation_id).owner
        result = tools.call(
            self.search,
            body.name,
            body.arguments,
            lambda id: self.read_upload(owner, id),
        )
        with Session(self.engine) as session, session.begin():
            run = authorized(session)
            version = result["result"]["index_version"]
            if run.index_version and run.index_version != version:
                raise HTTPException(
                    409, "The collection changed during exploration. Start a new turn."
                )
            run.index_version = version
            evidence = dict(run.evidence)
            for item in result["result"].get("results", []):
                evidence[item["image_id"]] = {
                    **item,
                    "index_version": result["result"]["index_version"],
                }
            run.evidence = evidence
            event(session, run, "tool", {"name": body.name})
        return result

    async def execute(self, payload, accept):
        folder = (
            self.settings.state_dir.resolve() / "threads" / payload["conversation_id"]
        )
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        env = {
            "PATH": os.defpath,
            "PYTHONPATH": str(ROOT / "backend"),
            "PYTHONUNBUFFERED": "1",
            "EXPLORER_THREAD_DIR": str(folder),
            "LOGFIRE_SEND_TO_LOGFIRE": "false",
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.explore.runner",
            cwd=folder,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=256 * 1024,
        )
        try:
            process.stdin.write(json.dumps(payload).encode() + b"\n")
            await process.stdin.drain()
            process.stdin.close()
            async for line in process.stdout:
                accept(json.loads(line))
            if await process.wait() != 0:
                raise RuntimeError("Codex worker failed")
        finally:
            # Own process group contains the worker, app server and MCP adapter.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            # Reap any surviving group members after an SDK shutdown failure.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
