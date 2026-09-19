"""Task-scoped execution tools. These operations never call the chat model."""

from typing import Literal

from pydantic import Field
from sqlalchemy.orm import Session

from app.agent_runtime.contracts import Contract
from app.services.agent.chat import ChatService
from app.services.agent.collection import read_collection_image
from app.services.agent.db import transaction
from app.services.agent.service import emit
from app.services.agent.storage import AgentError


class ExecutionResult(Contract):
    asset_ids: list[str] = Field(default_factory=list, max_length=1)
    source_message: str = Field(default="", max_length=4000)
    error_code: (
        Literal[
            "source_unavailable",
            "collection_unavailable",
            "collection_record_missing",
            "collection_record_ambiguous",
        ]
        | None
    ) = None


class ExecutionGateway:
    def __init__(self, service):
        self.service = service

    def execute(self, claims, tool):
        svc = self.service
        with Session(svc.engine) as session:
            run = svc.task(session, claims)
            if run.request.get("mode") != "execution":
                raise AgentError(
                    "invalid_operation", "An execution task is required.", 403
                )
            task = run.request["execution"]
        if tool.operation == "execution_image":
            if tool.arguments or not task.get("image_id"):
                raise AgentError(
                    "invalid_arguments",
                    "Only the compiled image ID may be loaded.",
                    422,
                )
            with Session(svc.engine) as session:
                run = svc.task(session, claims)
                if run.checkpoint.get("source_asset_id"):
                    return {"asset_id": run.checkpoint["source_asset_id"]}
            data, source = read_collection_image(svc.settings, task["image_id"])
            asset = svc.storage.save(data, svc.settings.workspace, "subject", source)
            with transaction(svc.engine) as session:
                run = svc.task(session, claims, True)
                if run.checkpoint.get("source_asset_id"):
                    return {"asset_id": run.checkpoint["source_asset_id"]}
                row = ChatService(svc).conversation(
                    session, run.request["conversation_id"], True
                )
                session.add(asset)
                row.asset_ids = list(dict.fromkeys([*row.asset_ids, asset.id]))
                row.subject_asset_id = asset.id
                run.request = {**run.request, "subject_asset_ids": [asset.id]}
                run.checkpoint = {**run.checkpoint, "source_asset_id": asset.id}
                emit(
                    session,
                    run,
                    "image_selected",
                    "The execution source image was resolved.",
                )
                return {"asset_id": asset.id}
        if tool.operation != "execution_finish":
            raise AgentError("invalid_operation", "Unsupported execution tool.", 422)
        result = ExecutionResult.model_validate(tool.arguments)
        with transaction(svc.engine) as session:
            run = svc.task(session, claims, True)
            generated = run.checkpoint.get("generated", [])
            evaluations = run.checkpoint.get("evaluations", {})
            if result.error_code:
                if result.asset_ids or generated:
                    raise AgentError(
                        "invalid_result",
                        "Source failures cannot publish generated images.",
                        422,
                    )
                status = "failed"
            else:
                if not result.asset_ids or any(
                    i not in generated or i not in evaluations for i in result.asset_ids
                ):
                    raise AgentError(
                        "invalid_result",
                        "Publish only evaluated outputs from this task.",
                        403,
                    )
                status = "succeeded"
            run.result = {
                "asset_ids": result.asset_ids,
                "execution_status": status,
                "source_message": result.source_message,
                "review_status": "needs_review"
                if any(evaluations[i]["action"] != "accept" for i in result.asset_ids)
                else "accepted",
            }
            run.error_code = result.error_code
            run.status, run.stage = "execution_done", "returning_to_chat"
            emit(
                session,
                run,
                "execution_done",
                "Execution results returned to the chat service.",
            )
            return {"status": run.status}
