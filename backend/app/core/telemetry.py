"""Configure process tracing without collecting search content or credentials."""

import os
from contextlib import contextmanager, nullcontext

import logfire
from logfire.types import ExceptionCallbackHelper
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.trace import Status, StatusCode

from app.core.config import ROOT, Settings

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
