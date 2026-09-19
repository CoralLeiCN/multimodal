import hashlib
import json
import time
from contextlib import nullcontext

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_models import Asset, Brand, Call, Event, Run, Workspace, uid
from app.agent_runtime.contracts import BrandInput, RunInput
from app.services.agent.db import transaction
from app.services.agent.storage import AgentError, Storage

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
ACTIVE = {"starting", "running"}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def emit(session, run, kind, summary):
    run.event_sequence += 1
    run.updated_at = time.time()
    session.add(
        Event(run_id=run.id, sequence=run.event_sequence, kind=kind, summary=summary)
    )


class AgentService:
    def __init__(self, settings, engine, storage=None):
        self.settings, self.engine = settings, engine
        self.storage = storage or Storage(settings)

    def run(self, session, run_id, lock=False):
        query = select(Run).where(
            Run.id == run_id, Run.workspace == self.settings.workspace
        )
        if lock:
            query = query.with_for_update()
        run = session.scalar(query)
        if run is None:
            raise AgentError("not_found", "Run not found.", 404)
        return run

    def asset(self, session, asset_id):
        asset = session.get(Asset, asset_id)
        if asset is None or asset.workspace != self.settings.workspace:
            raise AgentError("not_found", "Asset not found.", 404)
        return asset

    def brand(self, session, brand_id):
        brand = session.get(Brand, brand_id)
        if brand is None or brand.workspace != self.settings.workspace:
            raise AgentError("not_found", "Brand version not found.", 404)
        return brand

    def upload(self, content, kind):
        asset = self.storage.save(content, self.settings.workspace, kind)
        with transaction(self.engine) as session:
            session.add(asset)
        return self.asset_view(asset)

    @staticmethod
    def asset_view(asset):
        return {
            "id": asset.id,
            "kind": asset.kind,
            "width": asset.width,
            "height": asset.height,
            "url": f"/api/v1/agent/assets/{asset.id}/file",
        }

    def create_brand(self, request: BrandInput, brand_id=None):
        with transaction(self.engine) as session:
            session.scalar(
                select(Workspace)
                .where(Workspace.id == self.settings.workspace)
                .with_for_update()
            )
            for asset_id in request.reference_asset_ids:
                if self.asset(session, asset_id).kind != "reference":
                    raise AgentError(
                        "invalid_reference", "Select brand reference images.", 422
                    )
            version = 1
            if brand_id:
                latest = session.scalar(
                    select(Brand)
                    .where(
                        Brand.workspace == self.settings.workspace,
                        Brand.brand_id == brand_id,
                    )
                    .order_by(Brand.version.desc())
                )
                if not latest:
                    raise AgentError("not_found", "Brand not found.", 404)
                version = latest.version + 1
            brand = Brand(
                id=uid(),
                workspace=self.settings.workspace,
                brand_id=brand_id or uid(),
                version=version,
                profile=request.model_dump(),
            )
            session.add(brand)
            session.flush()
            return self.brand_view(brand)

    @staticmethod
    def brand_view(brand):
        return {
            "id": brand.id,
            "brand_id": brand.brand_id,
            "version": brand.version,
            "personality": "",
            "typography": "",
            "illustration_style": "",
            **brand.profile,
        }

    def brands(self):
        with Session(self.engine) as session:
            return [
                self.brand_view(b)
                for b in session.scalars(
                    select(Brand)
                    .where(Brand.workspace == self.settings.workspace)
                    .order_by(Brand.created_at.desc())
                )
            ]

    def create_run(
        self, request: RunInput, key, db_session=None, require_execution=True
    ):
        body = request.model_dump()
        request_hash = digest(body)
        with (
            transaction(self.engine) if db_session is None else nullcontext(db_session)
        ) as session:
            session.scalar(
                select(Workspace)
                .where(Workspace.id == self.settings.workspace)
                .with_for_update()
            )
            existing = session.scalar(
                select(Run).where(
                    Run.workspace == self.settings.workspace, Run.idempotency_key == key
                )
            )
            if existing:
                if existing.request_hash != request_hash:
                    raise AgentError(
                        "idempotency_conflict",
                        "This submission key was already used for another request.",
                    )
                return self.run_view(session, existing)
            if (
                self.settings.blockers()
                if require_execution
                else not self.settings.gemini_api_key
            ):
                raise AgentError(
                    "not_configured", "Generation is not configured yet.", 503
                )
            self.brand(session, request.brand_version)
            for asset_id in request.subject_asset_ids:
                self.asset(session, asset_id)
            if bool(request.parent_run_id) != bool(request.selected_asset_id):
                raise AgentError(
                    "invalid_parent", "Choose a parent run and one of its results.", 422
                )
            if request.parent_run_id:
                parent = self.run(session, request.parent_run_id)
                if request.selected_asset_id not in parent.result.get("asset_ids", []):
                    raise AgentError(
                        "invalid_parent",
                        "The selected image does not belong to this result.",
                        422,
                    )
                if request.subject_asset_ids:
                    raise AgentError(
                        "invalid_parent",
                        "An edit uses the selected result as its subject.",
                        422,
                    )
                body["subject_asset_ids"] = [request.selected_asset_id]
            queued = session.scalar(
                select(func.count())
                .select_from(Run)
                .where(
                    Run.workspace == self.settings.workspace,
                    Run.status.in_(
                        [
                            "queued",
                            "waiting_for_input",
                            "chat_queued",
                            "chat_preparing",
                            "chat_returning",
                            "execution_done",
                            *ACTIVE,
                        ]
                    ),
                )
            )
            if queued >= self.settings.max_queued_runs:
                raise AgentError(
                    "queue_full",
                    "The workspace queue is full. Finish or cancel a run first.",
                    429,
                )
            run = Run(
                id=uid(),
                workspace=self.settings.workspace,
                idempotency_key=key,
                request_hash=request_hash,
                brand_version=request.brand_version,
                request=body,
                event_sequence=0,
                checkpoint={},
                result={},
            )
            session.add(run)
            session.flush()
            emit(session, run, "queued", "Waiting for an agent worker.")
            return self.run_view(session, run)

    def run_view(self, session, run):
        artifacts = []
        for asset_id in run.result.get("asset_ids", []):
            asset = self.asset(session, asset_id)
            artifacts.append(
                {
                    **self.asset_view(asset),
                    "evaluation": run.checkpoint.get("evaluations", {}).get(asset_id),
                }
            )
        return {
            "id": run.id,
            "status": run.status,
            "stage": run.stage,
            "prompt": run.request["prompt"],
            "brand_version": run.brand_version,
            "request": run.request,
            "question": run.question,
            "error_code": run.error_code,
            "review_status": run.result.get("review_status"),
            "artifacts": artifacts,
            "trace_id": run.trace_id,
            "sandbox_id": run.sandbox_id,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }

    def read_run(self, run_id):
        with Session(self.engine) as session:
            return self.run_view(session, self.run(session, run_id))

    def runs(self):
        with Session(self.engine) as session:
            return [
                self.run_view(session, r)
                for r in session.scalars(
                    select(Run)
                    .where(Run.workspace == self.settings.workspace)
                    .order_by(Run.created_at.desc())
                    .limit(30)
                )
            ]

    def cancel(self, run_id):
        with transaction(self.engine) as session:
            run = self.run(session, run_id, True)
            if run.status not in TERMINAL:
                run.status, run.stage = "cancelled", "cancelled"
                run.result = {
                    "asset_ids": run.checkpoint.get("generated", []),
                    "review_status": "needs_review",
                }
                emit(
                    session,
                    run,
                    "cancelled",
                    "Cancelled. Already submitted model calls may still be charged.",
                )
            return self.run_view(session, run)

    def provide_input(self, run_id, answer):
        with transaction(self.engine) as session:
            run = self.run(session, run_id, True)
            if run.status != "waiting_for_input":
                raise AgentError("not_waiting", "This run is not waiting for input.")
            checkpoint = dict(run.checkpoint)
            answers = list(checkpoint.get("answers", []))
            if len(answers) >= 3:
                raise AgentError(
                    "input_limit", "Start a new run with a complete brief."
                )
            answers.append(answer)
            checkpoint["answers"] = answers
            run.checkpoint = checkpoint
            run.status, run.stage, run.question = "queued", "queued", None
            # Keep old sandbox identity until the worker confirms its termination.
            emit(session, run, "queued", "Additional direction received.")
            return self.run_view(session, run)

    def events(self, run_id, after):
        with Session(self.engine) as session:
            run = self.run(session, run_id)
            events = session.scalars(
                select(Event)
                .where(Event.run_id == run_id, Event.sequence > after)
                .order_by(Event.sequence)
                .limit(100)
            ).all()
            return [
                {
                    "sequence": e.sequence,
                    "kind": e.kind,
                    "summary": e.summary,
                    "created_at": e.created_at,
                }
                for e in events
            ], run.status

    def task(self, session, claims, lock=False):
        run = self.run(session, claims["run_id"], lock)
        if (
            run.attempt_id != claims["attempt_id"]
            or run.status != "running"
            or not run.sandbox_id
            or not run.deadline
            or run.deadline <= time.time()
        ):
            raise AgentError(
                "attempt_expired", "The run attempt is no longer active.", 403
            )
        return run

    def manifest(self, claims):
        with Session(self.engine) as session:
            run = self.task(session, claims)
            brand = self.brand(session, run.brand_version)
            if run.request.get("mode") == "execution":
                task = run.request["execution"]
                return {
                    "run_id": run.id,
                    "request": {"mode": "execution"},
                    "execution": task,
                    "brand": task["brand"],
                    "checkpoint": {
                        k: v
                        for k, v in run.checkpoint.items()
                        if k in {"generated", "evaluations", "revision_used"}
                    },
                }
            return {
                "run_id": run.id,
                "request": run.request,
                "brand": brand.profile,
                "checkpoint": run.checkpoint,
            }

    def allowed_assets(self, session, run):
        brand = self.brand(session, run.brand_version)
        return set(
            brand.profile["reference_asset_ids"]
            + run.request["subject_asset_ids"]
            + run.checkpoint.get("generated", [])
        )

    def heartbeat(self, claims):
        with transaction(self.engine) as session:
            run = self.task(session, claims, True)
            run.lease_until = time.time() + 90
            return {"status": run.status}

    def reserve(self, claims, tool):
        """Commit intent before a paid call. Never replay an ambiguous submission."""
        with transaction(self.engine) as session:
            run = self.task(session, claims, True)
            existing = session.scalar(
                select(Call).where(Call.run_id == run.id, Call.step_id == tool.step_id)
            )
            fingerprint = digest(tool.model_dump(exclude={"step_id"}))
            if existing:
                if existing.request_hash != fingerprint:
                    raise AgentError(
                        "step_conflict", "This step has different arguments."
                    )
                if existing.status == "completed":
                    return existing.response
                raise AgentError(
                    "outcome_unknown",
                    "The previous call may have executed. It will not be repeated.",
                )
            is_revision = tool.operation == "generate" and bool(
                tool.arguments.get("revision_asset_id")
            )
            if is_revision and session.scalar(
                select(Call.id)
                .where(Call.run_id == run.id, Call.is_revision.is_(True))
                .limit(1)
            ):
                raise AgentError(
                    "revision_limit", "This run already reserved its revision."
                )
            counts = dict(
                session.execute(
                    select(Call.operation, func.count())
                    .where(Call.run_id == run.id)
                    .group_by(Call.operation)
                ).all()
            )
            limit = {
                "plan": 8,
                "generate": run.request["candidate_count"] + 1,
                "evaluate": run.request["candidate_count"] + 1,
            }[tool.operation]
            if tool.operation == "generate" and not tool.arguments.get(
                "revision_asset_id"
            ):
                limit = run.request["candidate_count"]
            decisions = counts.get("plan", 0) + counts.get("evaluate", 0)
            if counts.get(tool.operation, 0) >= limit or (
                tool.operation in {"plan", "evaluate"} and decisions >= 8
            ):
                raise AgentError(
                    "budget_exceeded", "This run has reached its model call limit."
                )
            session.add(
                Call(
                    run_id=run.id,
                    step_id=tool.step_id,
                    operation=tool.operation,
                    is_revision=is_revision,
                    request_hash=fingerprint,
                )
            )
            run.stage = {
                "plan": "planning",
                "generate": "generating",
                "evaluate": "evaluating",
            }[tool.operation]
            emit(
                session,
                run,
                run.stage,
                {
                    "plan": "Preparing the creative brief.",
                    "generate": "Generating an image.",
                    "evaluate": "Checking subject and brand alignment.",
                }[tool.operation],
            )
        return None
