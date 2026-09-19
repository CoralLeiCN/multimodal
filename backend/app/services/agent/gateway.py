import logging
import time

import logfire
from google.genai.errors import APIError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_models import Call
from app.agent_runtime.contracts import (
    EvaluateInput,
    FinishInput,
    GenerateInput,
    WaitInput,
)
from app.services.agent.db import transaction
from app.services.agent.service import digest, emit
from app.services.agent.storage import AgentError


class Gateway:
    def __init__(self, service, provider):
        self.service, self.provider = service, provider

    def execute(self, claims, tool):
        service = self.service
        if tool.operation.startswith("execution_"):
            from app.services.agent.execution import ExecutionGateway

            return ExecutionGateway(service).execute(claims, tool)
        with Session(service.engine) as session:
            run = service.task(session, claims)
            existing = session.scalar(
                select(Call).where(Call.run_id == run.id, Call.step_id == tool.step_id)
            )
            if existing:
                if existing.request_hash != digest(
                    tool.model_dump(exclude={"step_id"})
                ):
                    raise AgentError(
                        "step_conflict", "This step has different arguments."
                    )
                if existing.status == "completed":
                    return existing.response
                raise AgentError(
                    "outcome_unknown",
                    "This call may have executed and will not be repeated.",
                )
            brief = dict(run.request)
            brand = dict(service.brand(session, run.brand_version).profile)
            if run.request.get("mode") == "execution":
                if tool.operation not in {"generate", "evaluate"}:
                    raise AgentError(
                        "invalid_operation",
                        "The sandbox accepts execution tools only.",
                        403,
                    )
                brand = run.request["execution"]["brand"]
            allowed = service.allowed_assets(session, run)
            generated = run.checkpoint.get("generated", [])
            answers = run.checkpoint.get("answers", [])
            if tool.operation == "generate":
                args = GenerateInput.model_validate(tool.arguments)
                if run.request.get("mode") == "execution":
                    task = run.request["execution"]
                    if task.get("image_id") and not run.checkpoint.get(
                        "source_asset_id"
                    ):
                        raise AgentError(
                            "source_missing",
                            "Resolve the compiled source before generating.",
                            409,
                        )
                    if not args.revision_asset_id and args.prompt != task["prompt"]:
                        raise AgentError(
                            "invalid_arguments",
                            "Use the compiled execution prompt.",
                            422,
                        )
                    if args.aspect_ratio and args.aspect_ratio != task["aspect_ratio"]:
                        raise AgentError(
                            "invalid_arguments", "Use the compiled aspect ratio.", 422
                        )
                if args.aspect_ratio:
                    brief["aspect_ratio"] = args.aspect_ratio
                if not set(args.reference_asset_ids).issubset(
                    brand["reference_asset_ids"]
                ):
                    raise AgentError(
                        "invalid_reference", "Reference not in this brand version.", 403
                    )
                if args.revision_asset_id and args.revision_asset_id not in generated:
                    raise AgentError(
                        "invalid_reference", "Revision image not in this run.", 403
                    )
                if (
                    len(generated) >= brief["candidate_count"]
                    and not args.revision_asset_id
                ):
                    raise AgentError(
                        "candidate_limit", "Only one additional revision is allowed."
                    )
                if args.revision_asset_id and run.checkpoint.get("revision_used"):
                    raise AgentError(
                        "revision_limit", "This run already used its revision."
                    )
                ids = [("subject", i) for i in brief["subject_asset_ids"]]
                ids += [("style", i) for i in args.reference_asset_ids]
                if args.revision_asset_id:
                    ids += [("revision", args.revision_asset_id)]
            elif tool.operation == "evaluate":
                args = EvaluateInput.model_validate(tool.arguments)
                if args.asset_id not in generated:
                    raise AgentError(
                        "invalid_reference", "Evaluate a candidate from this run.", 403
                    )
                ids = [("subject", i) for i in brief["subject_asset_ids"]]
                ids += [("style", i) for i in brand["reference_asset_ids"]]
                ids += [("candidate", args.asset_id)]
            elif tool.operation == "plan":
                if tool.arguments:
                    raise AgentError(
                        "invalid_arguments",
                        "Planning does not accept custom arguments.",
                        422,
                    )
                ids = [("subject", i) for i in brief["subject_asset_ids"]] + [
                    ("style", i) for i in brand["reference_asset_ids"]
                ]
            else:
                return self.transition(claims, tool)
            if any(i not in allowed for _, i in ids):
                raise AgentError(
                    "invalid_reference", "Asset is not available to this run.", 403
                )
            # Fetch before reserving a paid attempt. Asset retrieval failures are safe to retry.
            assets = []
            for role, asset_id in ids:
                asset = service.asset(session, asset_id)
                assets.append((role, asset, service.storage.get(asset)))
        cached = service.reserve(claims, tool)
        if cached is not None:
            return cached
        artifact = None
        try:
            if tool.operation == "plan":
                result, usage = self.provider.plan(brief, brand, answers, assets)
                if not set(result["reference_asset_ids"]).issubset(
                    brand["reference_asset_ids"]
                ):
                    raise AgentError(
                        "invalid_model_output",
                        "The model selected an unavailable reference.",
                        502,
                    )
            elif tool.operation == "generate":
                content, usage = self.provider.generate(
                    brief, brand, args.prompt, assets
                )
                artifact = service.storage.save(
                    content,
                    service.settings.workspace,
                    "generated",
                    {
                        "type": "generated",
                        "run_id": claims["run_id"],
                        "step_id": tool.step_id,
                        "reference_ids": [i for _, i in ids],
                        "model": service.settings.image_model,
                    },
                )
                numerator, denominator = map(int, brief["aspect_ratio"].split(":"))
                if (
                    abs(
                        artifact.width / artifact.height / (numerator / denominator) - 1
                    )
                    > 0.1
                ):
                    raise AgentError(
                        "invalid_output_dimensions",
                        "The generated image has the wrong aspect ratio.",
                        502,
                    )
                result = {"asset_id": artifact.id}
            else:
                result, usage = self.provider.evaluate(brief, brand, assets)
        except Exception as error:  # noqa: BLE001 -- mark ambiguous paid calls without leaking provider data
            # A network failure may occur after billing. No automatic paid retries.
            code = error.code if isinstance(error, AgentError) else "outcome_unknown"
            if isinstance(error, ValidationError):
                code = "invalid_model_output"
            http_status = error.code if isinstance(error, APIError) else None
            if http_status is not None:
                code = {
                    400: "provider_invalid_request",
                    401: "provider_authentication",
                    403: "provider_permission_denied",
                    404: "provider_model_unavailable",
                    429: "provider_rate_limited",
                }.get(
                    http_status,
                    "provider_unavailable"
                    if http_status >= 500
                    else "provider_rejected",
                )
            diagnostic = {
                "operation": tool.operation,
                "exception_type": type(error).__name__,
                "http_status": http_status,
                "model": getattr(
                    service.settings,
                    {"evaluate": "evaluation_model", "generate": "image_model"}.get(
                        tool.operation, "model"
                    ),
                ),
            }
            # Never log raw exceptions: provider messages may contain credentials or image data.
            logging.getLogger(__name__).warning(
                "Provider call failed run=%s step=%s code=%s diagnostic=%s",
                claims["run_id"],
                tool.step_id,
                code,
                diagnostic,
            )
            logfire.warn(
                "Agent provider call failed",
                run_id=claims["run_id"],
                step_id=tool.step_id,
                error_code=code,
                **diagnostic,
            )
            with transaction(service.engine) as session:
                run = service.run(session, claims["run_id"], True)
                call = session.scalar(
                    select(Call).where(
                        Call.run_id == run.id, Call.step_id == tool.step_id
                    )
                )
                call.status, call.error_code = "failed", code
                call.usage = {"diagnostic": diagnostic}
                if run.status == "running" and run.attempt_id == claims["attempt_id"]:
                    run.status, run.stage, run.error_code = "failed", "failed", code
                    run.result = {
                        "asset_ids": run.checkpoint.get("generated", []),
                        "review_status": "needs_review",
                        "failed_operation": tool.operation,
                        "diagnostic": diagnostic,
                    }
                    emit(
                        session,
                        run,
                        "failed",
                        "The model call could not be completed safely. Existing results are preserved.",
                    )
            raise AgentError(
                code,
                "The model call failed. It will not be automatically repeated.",
                502,
            ) from None
        with transaction(service.engine) as session:
            run = service.run(session, claims["run_id"], True)
            call = session.scalar(
                select(Call).where(Call.run_id == run.id, Call.step_id == tool.step_id)
            )
            call.response, call.usage, call.status = result, usage, "completed"
            # Keep audit data, but never publish a late cancelled or fenced result.
            if (
                run.status != "running"
                or run.attempt_id != claims["attempt_id"]
                or run.deadline <= time.time()
            ):
                raise_after = True
            else:
                raise_after = False
                checkpoint = dict(run.checkpoint)
                if artifact:
                    session.add(artifact)
                    checkpoint["generated"] = [
                        *checkpoint.get("generated", []),
                        artifact.id,
                    ]
                    if args.revision_asset_id:
                        checkpoint["revision_used"] = True
                elif tool.operation == "plan":
                    checkpoint["plan"] = result
                else:
                    checkpoint["evaluations"] = {
                        **checkpoint.get("evaluations", {}),
                        args.asset_id: result,
                    }
                run.checkpoint = checkpoint
                run.lease_until = time.time() + 90
                emit(
                    session,
                    run,
                    "step_completed",
                    f"{tool.operation.capitalize()} step completed.",
                )
        if raise_after:
            raise AgentError(
                "attempt_expired", "The attempt ended before this result arrived.", 403
            )
        return result

    def transition(self, claims, tool):
        service = self.service
        with transaction(service.engine) as session:
            run = service.task(session, claims, True)
            if tool.operation == "wait":
                args = WaitInput.model_validate(tool.arguments)
                run.status, run.stage, run.question = (
                    "waiting_for_input",
                    "waiting_for_input",
                    args.question,
                )
                emit(
                    session,
                    run,
                    "waiting_for_input",
                    "More creative direction is needed.",
                )
            elif tool.operation == "finish":
                args = FinishInput.model_validate(tool.arguments)
                if len(set(args.asset_ids)) != len(args.asset_ids) or not set(
                    args.asset_ids
                ).issubset(run.checkpoint.get("generated", [])):
                    raise AgentError(
                        "invalid_result",
                        "Only this run's candidates can be published.",
                        403,
                    )
                evaluations = run.checkpoint.get("evaluations", {})
                if any(i not in evaluations for i in args.asset_ids):
                    raise AgentError(
                        "evaluation_missing", "Evaluate every result before publishing."
                    )
                needs_review = any(
                    evaluations[i]["action"] != "accept" for i in args.asset_ids
                )
                run.result = {
                    "asset_ids": args.asset_ids,
                    "review_status": "needs_review" if needs_review else "accepted",
                }
                run.status, run.stage = "succeeded", "completed"
                emit(session, run, "succeeded", "Your images are ready to review.")
            else:
                raise AgentError("invalid_operation", "Unsupported operation.", 422)
            return {"status": run.status}
