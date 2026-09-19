import io
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.agent_models import Call, TraceBatch
from app.agent_runtime.contracts import BrandInput, RunInput, ToolRequest
from app.agent_runtime.runner import run_agent
from app.core.telemetry import accept_trace
from app.services.agent.application import create_agent_app
from app.services.agent.auth import task_token, verify
from app.services.agent.config import AgentSettings
from app.services.agent.db import engine_for, migrate, transaction
from app.services.agent.gateway import Gateway
from app.services.agent.service import AgentService
from app.services.agent.storage import AgentError
from app.services.agent.worker import Worker
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def picture(color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(stream, "PNG")
    return stream.getvalue()


def test_runtime_entrypoint_initializes_tracing_before_running_agent(monkeypatch):
    from types import SimpleNamespace

    from app.agent_runtime import runner
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    processors = []
    calls = []
    monkeypatch.setenv("AGENT_GATEWAY_URL", "https://gateway.example")
    monkeypatch.setenv("AGENT_TASK_TOKEN", "test-task-token")
    monkeypatch.setenv("AGENT_TRACEPARENT", "00-" + "a" * 32 + "-" + "b" * 16 + "-01")
    monkeypatch.setattr(runner, "OTLPSpanExporter", lambda **kw: ConsoleSpanExporter())
    monkeypatch.setattr(
        runner.logfire,
        "configure",
        lambda **kw: processors.extend(kw["additional_span_processors"]),
    )
    monkeypatch.setattr(runner.logfire, "force_flush", lambda **kw: None)
    client = SimpleNamespace(
        get=lambda path: calls.append(path) or {},
        client=SimpleNamespace(close=lambda: calls.append("close")),
    )
    monkeypatch.setattr(runner, "GatewayClient", lambda *args: client)
    monkeypatch.setattr(runner, "run_agent", lambda c: calls.append("agent"))
    try:
        assert runner.main() == 0
        assert calls == ["manifest", "agent", "close"]
        assert len(processors) == 1
    finally:
        for processor in processors:
            processor.shutdown()


class FakeProvider:
    def __init__(self):
        self.generated = 0
        self.evaluated = 0
        self.fail = False
        self.ask = False
        self.cancel = None

    def plan(self, brief, brand, answers, assets):
        return {
            "summary": "An editorial product image",
            "prompt": "Create a red object on ivory.",
            "reference_asset_ids": brand["reference_asset_ids"],
            "question": "Which object?" if self.ask and not answers else None,
        }, {"total_token_count": 10}

    def generate(self, brief, brand, prompt, assets):
        self.generated += 1
        if self.cancel:
            self.cancel()
        if self.fail:
            raise TimeoutError("sensitive provider text must not leak")
        return picture(), {"total_token_count": 20}

    def evaluate(self, brief, brand, assets):
        self.evaluated += 1
        return {
            "subject_score": 85,
            "brand_score": 60 if self.evaluated == 1 else 90,
            "request_score": 80,
            "summary": "Improve the brand colors."
            if self.evaluated == 1
            else "Aligned with the brand.",
            "action": "revise" if self.evaluated == 1 else "accept",
            "revision_prompt": "Use the brand palette.",
        }, None


class FakeModal:
    def __init__(self):
        self.names = {}
        self.codes = {}
        self.created = 0
        self.uncertain = False

    def create(self, run):
        self.created += 1
        self.names[run.sandbox_name] = f"sb-{self.created}"
        self.codes[f"sb-{self.created}"] = None
        if self.uncertain:
            raise TimeoutError()
        return f"sb-{self.created}"

    def find(self, name):
        return self.names.get(name)

    def poll(self, sandbox_id):
        return self.codes[sandbox_id]

    def terminate(self, sandbox_id):
        self.codes[sandbox_id] = 137


@pytest.fixture
def agent(tmp_path):
    settings = AgentSettings(
        _env_file=None,
        enabled=True,
        access_token="test-access-" + "x" * 32,
        database_url=f"sqlite:///{tmp_path}/agent.sqlite3",
        asset_root=tmp_path / "assets",
        gateway_url="https://gateway.example",
        gemini_api_key="fake-key",
        logfire_token=None,
    )
    engine = engine_for(settings)
    migrate(engine, settings.workspace)
    service = AgentService(settings, engine)
    ref = service.upload(picture(), "reference")
    subject = service.upload(picture("blue"), "subject")
    brand = service.create_brand(
        BrandInput(
            name="Fieldwork",
            description="Warm editorial imagery",
            reference_asset_ids=[ref["id"]],
        )
    )
    yield service, brand, subject, FakeProvider(), FakeModal()
    engine.dispose()


def new_run(agent, key="request-1", **kwargs):
    service, brand, subject, _, _ = agent
    body = RunInput(
        brand_version=brand["id"],
        prompt="A brass microscope",
        subject_asset_ids=[subject["id"]],
        **kwargs,
    )
    return service.create_run(body, key)


def activate(agent, run_id):
    service, _, _, _, manager = agent
    Worker(service, manager).tick()
    with Session(service.engine) as session:
        run = service.run(session, run_id)
        token = task_token(service.settings, run)
        return verify(service.settings, token, "task")


class DirectClient:
    def __init__(self, service, provider, claims):
        self.service, self.claims = service, claims
        self.gateway = Gateway(service, provider)

    def get(self, path):
        assert path == "manifest"
        return self.service.manifest(self.claims)

    def tool(self, step_id, operation, arguments=None):
        return self.gateway.execute(
            self.claims,
            ToolRequest(
                step_id=step_id, operation=operation, arguments=arguments or {}
            ),
        )


def test_complete_agent_flow_and_followup(agent):
    service, brand, _, provider, manager = agent
    created = new_run(agent)
    claims = activate(agent, created["id"])
    run_agent(DirectClient(service, provider, claims))
    result = service.read_run(created["id"])
    assert result["status"] == "succeeded"
    assert result["review_status"] == "accepted"
    assert len(result["artifacts"]) == 2
    assert provider.generated == 3
    assert provider.evaluated == 3
    assert result["trace_id"]
    followup = service.create_run(
        RunInput(
            brand_version=brand["id"],
            prompt="Change the background",
            parent_run_id=result["id"],
            selected_asset_id=result["artifacts"][0]["id"],
        ),
        "edit",
    )
    assert followup["request"]["subject_asset_ids"] == [result["artifacts"][0]["id"]]
    Worker(service, manager).tick()
    assert manager.codes["sb-1"] == 137
    assert service.read_run(created["id"])["sandbox_id"] == "sb-1"


def test_idempotency_and_versions_are_immutable(agent):
    service, brand, _, _, _ = agent
    first = new_run(agent)
    assert new_run(agent)["id"] == first["id"]
    with pytest.raises(AgentError, match="already used"):
        new_run(agent, candidate_count=1)
    newer = service.create_brand(
        BrandInput(name="New palette", description="Blue"), brand["brand_id"]
    )
    assert newer["version"] == 2
    assert service.read_run(first["id"])["brand_version"] == brand["id"]


def test_concurrent_duplicate_submission_claims_one_run(agent):
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: new_run(agent)["id"], range(2)))
    assert len(set(ids)) == 1


def test_paid_call_replay_and_ambiguous_failure(agent):
    service, _, _, provider, _ = agent
    run = new_run(agent, candidate_count=1)
    claims = activate(agent, run["id"])
    client = DirectClient(service, provider, claims)
    args = {"prompt": "Generate", "reference_asset_ids": []}
    first = client.tool("generate_0", "generate", args)
    assert client.tool("generate_0", "generate", args) == first
    assert provider.generated == 1
    with pytest.raises(AgentError):
        client.tool("generate_extra", "generate", args)
    provider.fail = True
    with pytest.raises(AgentError) as error:
        client.tool(
            "revision", "generate", {**args, "revision_asset_id": first["asset_id"]}
        )
    assert error.value.code == "outcome_unknown"
    result = service.read_run(run["id"])
    assert result["status"] == "failed"
    assert len(result["artifacts"]) == 1
    assert "sensitive" not in str(error.value)
    with pytest.raises(AgentError):
        client.tool(
            "revision", "generate", {**args, "revision_asset_id": first["asset_id"]}
        )
    assert provider.generated == 2


def test_cancel_fences_inflight_result(agent):
    service, _, _, provider, _ = agent
    run = new_run(agent)
    claims = activate(agent, run["id"])
    provider.cancel = lambda: service.cancel(run["id"])
    with pytest.raises(AgentError, match="ended"):
        DirectClient(service, provider, claims).tool(
            "generate_0", "generate", {"prompt": "Generate"}
        )
    assert service.read_run(run["id"])["artifacts"] == []
    assert service.read_run(run["id"])["status"] == "cancelled"
    with Session(service.engine) as session:
        call = session.scalar(select(Call))
        assert call.status == "completed"  # audit survives cancellation


def test_unknown_modal_create_is_reconciled_not_repeated(agent):
    service, _, _, _, manager = agent
    run = new_run(agent)
    manager.uncertain = True
    Worker(service, manager).tick()
    assert service.read_run(run["id"])["status"] == "starting"
    Worker(service, manager).tick()  # a restarted worker discovers the same sandbox
    assert service.read_run(run["id"])["status"] == "running"
    assert manager.created == 1


def test_deadline_revokes_tools_and_cleans_up(agent):
    service, _, _, provider, manager = agent
    run = new_run(agent)
    claims = activate(agent, run["id"])
    with transaction(service.engine) as session:
        service.run(session, run["id"], True).deadline = time.time() - 1
    with pytest.raises(AgentError):
        DirectClient(service, provider, claims).tool(
            "generate_0", "generate", {"prompt": "Generate"}
        )
    Worker(service, manager).tick()
    assert manager.codes["sb-1"] == 137
    assert service.read_run(run["id"])["status"] == "timed_out"
    assert provider.generated == 0


def test_clarification_releases_and_resumes(agent):
    service, _, _, provider, manager = agent
    provider.ask = True
    run = new_run(agent, candidate_count=1)
    claims = activate(agent, run["id"])
    run_agent(DirectClient(service, provider, claims))
    assert service.read_run(run["id"])["status"] == "waiting_for_input"
    service.provide_input(run["id"], "A microscope")
    Worker(service, manager).tick()
    assert manager.codes["sb-1"] == 137
    with Session(service.engine) as session:
        current = service.run(session, run["id"])
        next_claims = verify(
            service.settings, task_token(service.settings, current), "task"
        )
    with pytest.raises(AgentError):
        service.manifest(claims)
    run_agent(DirectClient(service, provider, next_claims))
    assert service.read_run(run["id"])["status"] == "succeeded"


def test_other_workspace_and_other_runs_assets_are_rejected(agent):
    service, _, _, provider, _ = agent
    run = new_run(agent)
    claims = activate(agent, run["id"])
    other = service.upload(picture("green"), "reference")
    with pytest.raises(AgentError):
        DirectClient(service, provider, claims).tool(
            "generate_0",
            "generate",
            {"prompt": "Generate", "reference_asset_ids": [other["id"]]},
        )
    settings = service.settings.model_copy(update={"workspace": "other"})
    outsider = AgentService(settings, service.engine)
    with pytest.raises(AgentError):
        outsider.read_run(run["id"])
    assert provider.generated == 0


def test_trace_relay_removes_content_and_rejects_other_trace(agent):
    import base64

    service, _, _, _, _ = agent
    run = new_run(agent)
    claims = activate(agent, run["id"])
    trace_id = service.read_run(run["id"])["trace_id"]
    batch = ExportTraceServiceRequest()
    span = (
        batch.resource_spans.add()
        .scope_spans.add()
        .spans.add(
            name="agent.run", trace_id=bytes.fromhex(trace_id), span_id=b"12345678"
        )
    )
    span.attributes.add(key="prompt").value.string_value = "sensitive brand details"
    span.events.add(name="secret exception")
    accept_trace(service, claims, batch.SerializeToString())
    with Session(service.engine) as session:
        stored = session.scalar(select(TraceBatch))
        assert b"sensitive" not in base64.b64decode(stored.payload)
        assert b"secret" not in base64.b64decode(stored.payload)
    span.trace_id = b"x" * 16
    with pytest.raises(AgentError):
        accept_trace(service, claims, batch.SerializeToString())


def test_api_auth_upload_sse_and_disabled_state(agent):
    service, _, _, _, _ = agent
    with TestClient(create_agent_app(service.settings)) as client:
        assert client.get("/api/v1/agent/brands").status_code == 401
        assert (
            client.post(
                "/api/v1/agent/session",
                json={"access_token": service.settings.access_token.get_secret_value()},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/agent/session",
                json={"access_token": service.settings.access_token.get_secret_value()},
            ).status_code
            == 200
        )
        assert client.cookies.get("agent_session")
        bad = client.post(
            "/api/v1/agent/assets",
            files={"file": ("fake.png", b"not an image", "image/png")},
            data={"kind": "reference"},
        )
        assert bad.status_code == 422
        assert (
            client.post(
                "/api/v1/agent/assets",
                files={"file": ("image.png", picture(), "image/png")},
                data={"kind": "reference"},
            ).status_code
            == 201
        )
        created = new_run(agent)
        service.cancel(created["id"])
        events = client.get(
            f"/api/v1/agent/runs/{created['id']}/events", headers={"Last-Event-ID": "1"}
        )
        assert "Cancelled" in events.text
        assert "Waiting for" not in events.text
        assert (
            client.post(
                "/api/v1/agent/internal/tools",
                json={"step_id": "x", "operation": "plan"},
            ).status_code
            == 401
        )
    with TestClient(create_agent_app(AgentSettings(_env_file=None))) as client:
        assert client.get("/api/v1/agent/status").json()["enabled"] is False
        assert client.get("/api/v1/agent/brands").status_code == 503


def test_budget_reservation_survives_process_loss(agent):
    service, _, _, provider, _ = agent
    run = new_run(agent)
    claims = activate(agent, run["id"])
    tool = ToolRequest(
        step_id="generation", operation="generate", arguments={"prompt": "Generate"}
    )
    service.reserve(claims, tool)
    with pytest.raises(AgentError) as error:
        Gateway(service, provider).execute(claims, tool)
    assert error.value.code == "outcome_unknown"
    assert provider.generated == 0
    with Session(service.engine) as session:
        assert session.scalar(select(func.count()).select_from(Call)) == 1


def test_production_rejects_local_storage_and_database(tmp_path):
    settings = AgentSettings(
        _env_file=None, environment="production", access_token="x" * 32
    )
    with pytest.raises(ValueError, match="PostgreSQL"):
        settings.validate_enabled()


def test_only_one_revision_can_be_reserved_concurrently(agent):
    service, _, _, _, _ = agent
    run = new_run(agent)
    claims = activate(agent, run["id"])

    def reserve(number):
        try:
            service.reserve(
                claims,
                ToolRequest(
                    step_id=f"revision_{number}",
                    operation="generate",
                    arguments={"prompt": "Revise", "revision_asset_id": "candidate"},
                ),
            )
            return "reserved"
        except AgentError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, range(2)))
    assert sorted(outcomes) == ["reserved", "revision_limit"]


def test_modal_adapter_has_scoped_network_and_no_provider_secrets(agent, monkeypatch):
    import types

    import app.services.agent.modal_sandbox as adapter

    service, _, _, _, _ = agent
    run = new_run(agent)
    activate(agent, run["id"])
    captured = {}
    monkeypatch.setattr(adapter.modal.App, "lookup", lambda *a, **kw: "app")
    monkeypatch.setattr(adapter, "runtime_image", lambda: "runtime-only-image")
    monkeypatch.setattr(adapter.modal.Secret, "from_dict", lambda value: value)

    def create(*args, **kwargs):
        captured.update(kwargs)
        captured["command"] = args
        return types.SimpleNamespace(object_id="sb-real-adapter")

    monkeypatch.setattr(adapter.modal.Sandbox, "create", create)
    with Session(service.engine) as session:
        row = service.run(session, run["id"])
        assert (
            adapter.ModalSandboxManager(service.settings).create(row)
            == "sb-real-adapter"
        )
    assert captured["outbound_domain_allowlist"] == ["gateway.example"]
    assert captured["outbound_cidr_allowlist"] == []
    assert captured["inbound_cidr_allowlist"] == []
    assert captured["command"] == ("python", "-m", "agent_runtime.runner")
    assert set(captured["secrets"][0]) == {"AGENT_TASK_TOKEN"}
    assert "fake-key" not in str(captured)
    assert captured["timeout"] == 600


def test_refusal_and_bad_dimensions_preserve_failure_without_retry(agent):
    import types

    from app.services.agent.provider import GeminiProvider

    service, _, _, _, _ = agent
    run = new_run(agent)
    claims = activate(agent, run["id"])
    provider = GeminiProvider.__new__(GeminiProvider)
    provider.settings = service.settings
    provider.client = types.SimpleNamespace(
        models=types.SimpleNamespace(
            generate_content=lambda **kw: types.SimpleNamespace(candidates=[])
        )
    )
    with pytest.raises(AgentError) as error:
        DirectClient(service, provider, claims).tool(
            "generate_0", "generate", {"prompt": "Generate"}
        )
    assert error.value.code == "provider_no_image"
    assert service.read_run(run["id"])["status"] == "failed"


def test_oversized_stream_and_corrupt_trace_rejected(agent):
    service, _, _, _, _ = agent
    with TestClient(create_agent_app(service.settings)) as client:
        response = client.post(
            "/api/v1/agent/runs",
            content=b"x" * (256 * 1024 + 1),
            headers={
                "Authorization": "Bearer "
                + service.settings.access_token.get_secret_value()
            },
        )
        assert response.status_code == 413
    run = new_run(agent)
    claims = activate(agent, run["id"])
    with pytest.raises(AgentError) as error:
        accept_trace(service, claims, b"not protobuf")
    assert error.value.code == "invalid_trace"


def test_orphan_cleanup_preserves_references_recent_files_and_other_workspaces(agent):
    import os

    service, _, _, _, _ = agent
    storage = service.storage
    orphan = storage.save(picture(), service.settings.workspace, "generated")
    referenced = storage.save(picture(), service.settings.workspace, "generated")
    recent = storage.save(picture(), service.settings.workspace, "generated")
    other = storage.save(picture(), "another-company", "generated")
    old = time.time() - 90000
    for asset in (orphan, referenced, other):
        os.utime(storage.root / asset.object_key, (old, old))
    assert (
        storage.prune_orphans(
            service.settings.workspace, {referenced.object_key}, time.time() - 86400
        )
        == 1
    )
    assert not (storage.root / orphan.object_key).exists()
    for asset in (referenced, recent, other):
        assert storage.get(asset) == picture()


def test_cancel_keeps_completed_candidates(agent):
    service, _, _, provider, _ = agent
    run = new_run(agent)
    claims = activate(agent, run["id"])
    result = DirectClient(service, provider, claims).tool(
        "generate_0", "generate", {"prompt": "Generate"}
    )
    service.cancel(run["id"])
    saved = service.read_run(run["id"])
    assert saved["status"] == "cancelled"
    assert [asset["id"] for asset in saved["artifacts"]] == [result["asset_id"]]
    assert saved["review_status"] == "needs_review"


def test_agent_instruments_genai_without_reconfiguring_search_tracing(monkeypatch):
    from app.core import telemetry

    calls = []
    monkeypatch.setattr(telemetry, "_configured", True)
    monkeypatch.setattr(telemetry, "_genai_instrumented", False)
    monkeypatch.setattr(
        telemetry.logfire, "configure", lambda **kw: calls.append("configure")
    )
    monkeypatch.setattr(
        telemetry.logfire, "instrument_google_genai", lambda: calls.append("genai")
    )
    monkeypatch.setenv(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_ONLY"
    )
    telemetry.configure(AgentSettings(_env_file=None))
    telemetry.configure(AgentSettings(_env_file=None))
    import os

    assert calls == ["genai"]
    assert (
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == "NO_CONTENT"
    )
