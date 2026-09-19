"""Configure process tracing without collecting search content or credentials."""

import base64
import os
import time
from contextlib import contextmanager, nullcontext

import httpx
import logfire
from google.protobuf.message import DecodeError
from logfire.types import ExceptionCallbackHelper
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.trace import Status, StatusCode
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_models import TraceBatch
from app.core.config import ROOT, Settings
from app.services.agent.db import transaction
from app.services.agent.storage import AgentError

_configured = False


def omit_query_string(span, _scope=None):
    if span.is_recording():
        for key in ("http.url", "http.target", "url.full"):
            value = (span.attributes or {}).get(key)
            if isinstance(value, str):
                span.set_attribute(key, value.partition("?")[0])
        if "url.query" in (span.attributes or {}):
            span.set_attribute("url.query", "")


class RequestPrivacy(SpanProcessor):
    """Remove query values before Logfire exports even an unfinished request span."""

    def on_start(self, span, parent_context=None):
        omit_query_string(span)


def redact_exception(helper: ExceptionCallbackHelper) -> None:
    # Provider and database exceptions can include request bodies or SQL values.
    error_type = type(helper.exception).__name__
    helper.span.set_attribute("error.type", error_type)
    helper.span.set_status(Status(StatusCode.ERROR, error_type))
    helper.no_record_exception()


@contextmanager
def indexer_destination():
    # The SDK also reads these variables directly. Keep the API's destination
    # from overriding the indexer's token or saved project during configuration.
    names = ("LOGFIRE_TOKEN", "LOGFIRE_API_KEY", "LOGFIRE_BASE_URL")
    saved = {name: os.environ.pop(name) for name in names if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def configure_telemetry(
    settings: Settings, service_name: str, *, indexing: bool = False
) -> None:
    global _configured
    if _configured:
        return
    token = settings.logfire_indexer_token if indexing else settings.logfire_token
    data_dir = ROOT / ".logfire"
    if indexing:
        data_dir /= "indexer"
    with indexer_destination() if indexing else nullcontext():
        logfire.configure(
            service_name=service_name,
            environment=settings.logfire_environment,
            send_to_logfire=settings.logfire_send_to_logfire,
            token=token.get_secret_value() if token else None,
            data_dir=data_dir,
            console=False,
            inspect_arguments=False,
            additional_span_processors=[RequestPrivacy()],
            advanced=logfire.AdvancedOptions(exception_callback=redact_exception),
        )
    _configured = True


def instrument_api(app) -> None:
    logfire.instrument_fastapi(
        app,
        capture_headers=False,
        request_attributes_mapper=lambda _request, _attributes: {},
        # ASGI instrumentation also sets attributes after the span starts.
        server_request_hook=omit_query_string,
    )


_genai_instrumented = False


def configure(settings):
    global _configured, _genai_instrumented
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "NO_CONTENT"
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
    if not _genai_instrumented:
        logfire.instrument_google_genai()
        _genai_instrumented = True


def accept_trace(service, claims, content):
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
