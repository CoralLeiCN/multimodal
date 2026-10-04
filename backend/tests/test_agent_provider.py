"""Exercise the real OpenAI SDK over a local mock transport, without paid calls."""

import base64
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import httpx
import openai
import pytest
from app.core.config import ROOT
from app.services.agent.config import AgentSettings
from app.services.agent.provider import AgentProvider
from app.services.agent.storage import AgentError


@pytest.fixture
def provider_factory(monkeypatch):
    original = openai.OpenAI
    providers = []

    def create(handle):
        def client(**kwargs):
            assert kwargs["max_retries"] == 0
            return original(
                http_client=httpx.Client(transport=httpx.MockTransport(handle)),
                **kwargs,
            )

        monkeypatch.setattr(openai, "OpenAI", client)
        provider = AgentProvider(
            AgentSettings(
                _env_file=None,
                openai_api_key="private-fixture-key",
                openai_base_url="https://compatible.example/v1",
                model="vision-fixture",
            )
        )
        providers.append(provider)
        return provider

    yield create
    for provider in providers:
        provider.close()


def response(output, *, finish_reason="stop", refusal=None):
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-fixture",
            "object": "chat.completion",
            "created": 1,
            "model": "vision-fixture",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(output) if output else None,
                        "refusal": refusal,
                    },
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        },
    )


@pytest.mark.parametrize(
    "operation,output",
    [
        ("chat", {"action": "reply", "message": "Hello", "execution": None}),
        (
            "plan",
            {
                "summary": "Ready",
                "prompt": "A blue object",
                "reference_asset_ids": [],
                "question": None,
            },
        ),
        (
            "evaluate",
            {
                "subject_score": 90,
                "brand_score": 90,
                "request_score": 90,
                "summary": "Matches",
                "action": "accept",
                "revision_prompt": "",
            },
        ),
    ],
)
def test_structured_decisions_and_images_use_configured_endpoint(
    provider_factory, operation, output
):
    requests = []

    def handle(request):
        assert str(request.url) == "https://compatible.example/v1/chat/completions"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["model"] == "vision-fixture"
        assert body["max_completion_tokens"] == 8192
        schema = body["response_format"]["json_schema"]
        assert schema["strict"] is True
        assert schema["schema"]["additionalProperties"] is False
        assert set(schema["schema"]["required"]) == set(schema["schema"]["properties"])
        requests.append(body)
        return response(output)

    provider = provider_factory(handle)
    assets = [
        (
            "subject",
            SimpleNamespace(id="fixture-id", mime="image/png"),
            b"fixture-image",
        )
    ]
    if operation == "chat":
        result, usage = provider.chat(
            {"messages": [{"role": "user", "content": "Hello"}]}, {}
        )
    elif operation == "plan":
        result, usage = provider.plan({}, {}, [], assets)
    else:
        result, usage = provider.evaluate({}, {}, assets)
    assert result == output
    assert usage["total_tokens"] == 20
    assert len(requests) == 1
    assert provider.image_provider is None
    if operation != "chat":
        content = requests[0]["messages"][1]["content"]
        assert content[1]["text"] == "subject; asset_id=fixture-id"
        assert (
            content[2]["image_url"]["url"]
            == "data:image/png;base64," + base64.b64encode(b"fixture-image").decode()
        )


@pytest.mark.parametrize("status", [429, 503])
def test_paid_requests_are_not_retried(provider_factory, status):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            status, json={"error": {"message": "private-provider-error"}}
        )

    provider = provider_factory(handle)
    with pytest.raises(openai.APIStatusError):
        provider.chat({"messages": []}, {})
    assert len(requests) == 1


@pytest.mark.parametrize("kind", ["refusal", "truncated", "invalid"])
def test_invalid_decisions_do_not_execute_or_retry(provider_factory, kind):
    requests = []

    def handle(request):
        requests.append(request)
        return response(
            {"action": "execute", "execution": None},
            refusal="Declined" if kind == "refusal" else None,
            finish_reason="length" if kind == "truncated" else "stop",
        )

    provider = provider_factory(handle)
    from pydantic import ValidationError

    with pytest.raises((AgentError, openai.LengthFinishReasonError, ValidationError)):
        provider.chat({"messages": []}, {})
    assert len(requests) == 1


def test_disabled_search_imports_no_agent_or_provider_runtime():
    code = """
import importlib.abc
import sys
class BlockProviders(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname in {'openai', 'google.genai'}:
            raise AssertionError('Search imported optional provider SDK: ' + fullname)
sys.meta_path.insert(0, BlockProviders())
from app.main import app
from fastapi.testclient import TestClient
client = TestClient(app)
assert client.get('/api/v1/agent/status').status_code == 404
assert not any(name.startswith('app.services.agent') for name in sys.modules)
assert '/api/v1/agent/runs' not in app.openapi()['paths']
assert 'app.agent_models' not in sys.modules
assert 'app.services.agent.provider' not in sys.modules
client.close()
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=True,
        env={
            **os.environ,
            "AGENT_ENABLED": "false",
            "LOGFIRE_SEND_TO_LOGFIRE": "false",
            "PYTHONPATH": str(ROOT / "backend"),
        },
    )


@pytest.mark.parametrize(
    "base_url,valid",
    [
        ("https://compatible.example/v1", True),
        ("http://127.0.0.1:8080/v1", True),
        ("http://remote.example/v1", False),
        ("https://key@remote.example/v1", False),
        ("https://remote.example/v1?key=secret", False),
    ],
)
def test_harness_settings_require_explicit_model_and_safe_endpoint(base_url, valid):
    settings = AgentSettings(
        _env_file=None,
        openai_api_key="fixture",
        model="fixture",
        openai_base_url=base_url,
    )
    assert (not settings.harness_blockers()) is valid
    settings.model = ""
    assert "AGENT_MODEL" in settings.harness_blockers()


def test_harness_spans_exclude_prompts_images_responses_and_provider_errors(
    provider_factory, monkeypatch
):
    import logfire
    from app.core import telemetry
    from logfire.testing import TestExporter as SpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    exporter = SpanExporter()
    logfire.configure(
        send_to_logfire=False,
        console=False,
        inspect_arguments=False,
        additional_span_processors=[SimpleSpanProcessor(exporter)],
        advanced=logfire.AdvancedOptions(exception_callback=telemetry.redact_exception),
    )
    monkeypatch.setattr(telemetry, "_configured", True)
    private = "private-fixture-content-87291"
    provider = provider_factory(
        lambda request: response(
            {"action": "reply", "message": private, "execution": None}
        )
    )
    provider.chat({"messages": [{"content": private}]}, {})
    failing = provider_factory(
        lambda request: httpx.Response(503, json={"error": {"message": private}})
    )
    with pytest.raises(openai.APIStatusError):
        failing.chat({"messages": []}, {})
    output = json.dumps(exporter.exported_spans_as_dict(_include_pending_spans=True))
    assert private not in output
    assert "private-fixture-key" not in output
    assert "input_tokens" in output
    assert "InternalServerError" in output
