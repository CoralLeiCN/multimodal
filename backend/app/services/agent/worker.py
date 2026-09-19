"""Durable task worker: run separately from the HTTP server."""

import argparse
import logging
import secrets
import time

import logfire
from opentelemetry import propagate
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_models import Asset, Run, Workspace, uid
from app.core.telemetry import configure, flush_relay
from app.services.agent.config import AgentSettings
from app.services.agent.db import engine_for, migrate, transaction
from app.services.agent.modal_sandbox import ModalSandboxManager, image_version
from app.services.agent.service import ACTIVE, AgentService, emit


class Worker:
    def __init__(self, service, manager, chat_provider=None):
        self.service, self.manager = service, manager
        from app.services.agent.chat_coordinator import ChatCoordinator

        self.chat = ChatCoordinator(service, chat_provider)

    def reconcile(self):
        service = self.service
        with Session(service.engine) as session:
            runs = session.scalars(
                select(Run).where(
                    Run.workspace == service.settings.workspace,
                    (Run.status.in_(ACTIVE)) | (Run.sandbox_name.is_not(None)),
                )
            ).all()
        for snapshot in runs:
            try:
                sandbox_id = snapshot.sandbox_id or self.manager.find(
                    snapshot.sandbox_name
                )
                code = self.manager.poll(sandbox_id) if sandbox_id else None
                terminate = False
                with transaction(service.engine) as session:
                    run = service.run(session, snapshot.id, True)
                    if run.attempt_id != snapshot.attempt_id:
                        continue
                    if sandbox_id:
                        run.sandbox_id = sandbox_id
                    if run.status in ACTIVE:
                        if run.deadline and time.time() >= run.deadline:
                            run.status, run.stage, run.error_code = (
                                "timed_out",
                                "timed_out",
                                "deadline_exceeded",
                            )
                            emit(
                                session,
                                run,
                                "timed_out",
                                "The run reached its time limit.",
                            )
                        elif sandbox_id and code is not None:
                            run.status, run.stage, run.error_code = (
                                "failed",
                                "failed",
                                "sandbox_exit",
                            )
                            run.result = {
                                "asset_ids": run.checkpoint.get("generated", []),
                                "review_status": "needs_review",
                            }
                            emit(
                                session,
                                run,
                                "failed",
                                "The sandbox stopped before publishing a result.",
                            )
                        elif run.status == "starting" and sandbox_id:
                            run.status, run.stage = "running", "planning"
                            run.lease_until = time.time() + 90
                            emit(session, run, "running", "The cloud agent is running.")
                        elif (
                            run.status == "starting"
                            and time.time() > run.created_at
                            and time.time() > run.lease_until
                        ):
                            run.status, run.stage, run.error_code = (
                                "failed",
                                "failed",
                                "sandbox_start_unknown",
                            )
                            emit(
                                session,
                                run,
                                "failed",
                                "Sandbox startup could not be confirmed. No automatic replacement was started.",
                            )
                        elif run.status == "running" and time.time() > run.lease_until:
                            run.status, run.stage, run.error_code = (
                                "failed",
                                "failed",
                                "heartbeat_expired",
                            )
                            emit(
                                session,
                                run,
                                "failed",
                                "The sandbox stopped reporting progress.",
                            )
                    if run.status in {"failed", "timed_out"}:
                        run.result = {
                            **run.result,
                            "asset_ids": run.checkpoint.get("generated", []),
                            "review_status": "needs_review",
                        }
                    terminate = run.status not in ACTIVE
                if terminate:
                    if sandbox_id:
                        self.manager.terminate(sandbox_id)
                    # Retain names after ambiguous creation until the maximum lifetime has passed.
                    if sandbox_id or time.time() > (snapshot.deadline or 0):
                        with transaction(service.engine) as session:
                            run = service.run(session, snapshot.id, True)
                            if run.attempt_id == snapshot.attempt_id:
                                run.sandbox_name = None
            except Exception:  # noqa: BLE001 -- reconcile uncertain cloud state on the next sweep
                logging.getLogger(__name__).warning(
                    "Sandbox reconciliation unavailable for run %s", snapshot.id
                )

    def tick(self):
        with logfire.span("sandbox.dispatch"):
            self._tick()

    def _tick(self):
        self.chat.tick()
        self.reconcile()
        service = self.service
        claimed = None
        with transaction(service.engine) as session:
            session.scalar(
                select(Workspace)
                .where(Workspace.id == service.settings.workspace)
                .with_for_update()
            )
            active = session.scalar(
                select(Run.id)
                .where(
                    Run.workspace == service.settings.workspace,
                    Run.status.in_([*ACTIVE, "chat_preparing", "chat_returning"]),
                )
                .limit(1)
            )
            if not active:
                run = session.scalar(
                    select(Run)
                    .where(
                        Run.workspace == service.settings.workspace,
                        Run.status == "queued",
                        Run.sandbox_name.is_(None),
                    )
                    .order_by(Run.created_at)
                    .with_for_update()
                    .limit(1)
                )
                if run:
                    run.sandbox_id = None
                    run.attempt_id = uid()
                    run.sandbox_name = f"image-{run.id}-{run.attempt_id[:8]}"
                    run.image_version = image_version()
                    run.status, run.stage = "starting", "starting"
                    run.deadline = (
                        time.time()
                        + service.settings.run_timeout
                        + service.settings.startup_timeout
                    )
                    run.lease_until = time.time() + service.settings.startup_timeout
                    carrier = {}
                    propagate.inject(carrier)
                    run.traceparent = (
                        carrier.get("traceparent")
                        or f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"
                    )
                    run.trace_id = run.traceparent.split("-")[1]
                    emit(session, run, "starting", "Starting an isolated cloud agent.")
                    claimed = run
        if claimed:
            try:
                sandbox_id = self.manager.create(claimed)
                with transaction(service.engine) as session:
                    run = service.run(session, claimed.id, True)
                    run.sandbox_id = sandbox_id
                    if run.status == "starting":
                        run.status, run.stage = "running", "planning"
                        run.deadline = min(
                            run.deadline, time.time() + service.settings.run_timeout
                        )
                        run.lease_until = time.time() + 90
                        emit(session, run, "running", "The cloud agent is running.")
                # Cancellation during create must still reclaim the newly created sandbox.
                if run.status != "running":
                    self.manager.terminate(sandbox_id)
            except Exception:  # noqa: BLE001 -- an uncertain create must not spawn a duplicate
                logging.getLogger(__name__).warning(
                    "Sandbox creation uncertain for run %s", claimed.id
                )
        flush_relay(service)
        try:
            with Session(service.engine) as session:
                keys = set(
                    session.scalars(
                        select(Asset.object_key).where(
                            Asset.workspace == service.settings.workspace
                        )
                    )
                )
            service.storage.prune_orphans(
                service.settings.workspace, keys, time.time() - 86400
            )
        except Exception:  # noqa: BLE001 -- storage maintenance must not replay or fail an agent run
            logging.getLogger(__name__).warning("Orphan asset cleanup unavailable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = AgentSettings()
    settings.validate_enabled()
    if not settings.gemini_api_key:
        raise SystemExit("Configure Gemini before starting the chat worker.")
    configure(settings)
    engine = engine_for(settings)
    migrate(engine, settings.workspace)
    worker = Worker(AgentService(settings, engine), ModalSandboxManager(settings))
    try:
        while True:
            worker.tick()
            if args.once:
                break
            time.sleep(5)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
