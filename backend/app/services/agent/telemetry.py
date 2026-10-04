"""Image Studio tracing and bounded task relay."""

import base64
import time

import httpx
import logfire
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from app.core.telemetry import RequestPrivacy, redact_exception

_configured = False


def configure(settings):
    global _configured
    if not _configured:
        token = (
            settings.logfire_token.get_secret_value()
            if settings.logfire_token
            else None
        )
        logfire.configure(
            token=token,
            send_to_logfire=bool(token),
            service_name="image-agent-gateway",
            console=False,
            inspect_arguments=False,
            additional_span_processors=[RequestPrivacy()],
            advanced=logfire.AdvancedOptions(
                base_url=settings.logfire_api_url,
                exception_callback=redact_exception,
            ),
        )
        _configured = True


def accept_trace(service, claims, content):
    from sqlalchemy import func, select

    from app.agent_models import TraceBatch
    from app.services.agent.db import transaction
    from app.services.agent.storage import AgentError

    if len(content) > 256 * 1024:
        raise AgentError("trace_too_large", "Trace batch exceeds the limit.", 413)
    incoming = ExportTraceServiceRequest()
    try:
        incoming.ParseFromString(content)
    except DecodeError:
        raise AgentError("invalid_trace", "Invalid trace batch.", 422) from None
    clean = ExportTraceServiceRequest()
    resource = clean.resource_spans.add()
    attribute = resource.resource.attributes.add(key="service.name")
    attribute.value.string_value = "image-agent-sandbox"
    scope = resource.scope_spans.add()
    scope.scope.name = "logfire"
    allowed_names = {
        "agent.run",
        "agent.chat",
        "tool.read_collection",
        "agent.plan",
        "tool.generate_image",
        "tool.evaluate_image",
        "tool.publish_result",
    }
    with transaction(service.engine) as session:
        run = service.run(session, claims["run_id"], True)
        if run.attempt_id != claims["attempt_id"] or run.status in {
            "cancelled",
            "timed_out",
        }:
            raise AgentError(
                "attempt_expired", "Trace attempt is no longer valid.", 403
            )
        count = 0
        for rs in incoming.resource_spans:
            for ss in rs.scope_spans:
                for span in ss.spans:
                    count += 1
                    if count > 128:
                        raise AgentError("trace_too_large", "Too many spans.", 413)
                    if span.trace_id.hex() != run.trace_id or len(span.span_id) != 8:
                        raise AgentError(
                            "invalid_trace",
                            "Trace does not belong to this attempt.",
                            403,
                        )
                    if span.name not in allowed_names:
                        continue
                    out = scope.spans.add()
                    out.trace_id, out.span_id, out.parent_span_id = (
                        span.trace_id,
                        span.span_id,
                        span.parent_span_id,
                    )
                    out.name, out.kind = span.name, span.kind
                    out.start_time_unix_nano, out.end_time_unix_nano = (
                        span.start_time_unix_nano,
                        span.end_time_unix_nano,
                    )
                    out.status.code = span.status.code
                    # Rebuild metadata; discard events, links, error text, prompts, and arbitrary attributes.
                    for key, value in {
                        "run_id": run.id,
                        "attempt_id": run.attempt_id,
                        "logfire.msg": span.name,
                        "logfire.span_type": "span",
                    }.items():
                        out.attributes.add(key=key).value.string_value = value
        queued = session.scalar(select(func.count()).select_from(TraceBatch))
        if queued >= 500:
            raise AgentError(
                "tracing_backlog", "Tracing is temporarily unavailable.", 503
            )
        if scope.spans:
            session.add(
                TraceBatch(payload=base64.b64encode(clean.SerializeToString()).decode())
            )
    return {"accepted": True}


def flush_relay(service):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.agent_models import TraceBatch
    from app.services.agent.db import transaction

    settings = service.settings
    if not settings.logfire_token:
        return
    # Export outside a write transaction so a slow collector cannot block heartbeats.
    with Session(service.engine) as session:
        batches = session.execute(
            select(TraceBatch.id, TraceBatch.created_at, TraceBatch.payload)
            .order_by(TraceBatch.created_at)
            .limit(10)
        ).all()
    for batch in batches:
        if batch.created_at >= time.time() - 86400:
            try:
                response = httpx.post(
                    settings.logfire_api_url.rstrip("/") + "/v1/traces",
                    content=base64.b64decode(batch.payload),
                    timeout=5,
                    headers={
                        "Authorization": f"Bearer {settings.logfire_token.get_secret_value()}",
                        "Content-Type": "application/x-protobuf",
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError:
                break
        with transaction(service.engine) as session:
            stored = session.get(TraceBatch, batch.id)
            if stored:
                session.delete(stored)
