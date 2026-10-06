import copy
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from app.agent_models import Call
from app.agent_runtime.contracts import (
    ConversationInput,
    ExecutionRequest,
    MessageInput,
    ToolRequest,
)
from app.agent_runtime.runner import run_agent
from app.services.agent.application import create_agent_app
from app.services.agent.auth import task_token, verify
from app.services.agent.chat import ChatService
from app.services.agent.collection import read_collection_image
from app.services.agent.db import transaction
from app.services.agent.service import AgentService
from app.services.agent.storage import AgentError
from app.services.agent.worker import Worker
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_agent import DirectClient, FakeProvider, picture
from test_agent import agent as agent  # noqa: PLC0414 -- shared isolated fixture


class ChatProvider(FakeProvider):
    def __init__(self, decisions):
        super().__init__()
        self.evaluated = 1
        self.decisions = list(decisions)
        self.contexts = []
        self.inputs = []
        self.effective_evaluation_brands = []

    def chat(self, context, brand):
        self.contexts.append(copy.deepcopy(context))
        return self.decisions.pop(0), {"total_tokens": 12}

    def evaluate(self, brief, brand, assets):
        self.effective_evaluation_brands.append(copy.deepcopy(brand))
        return super().evaluate(brief, brand, assets)

    def generate(self, brief, brand, prompt, assets):
        self.inputs.append(
            (copy.deepcopy(brief), copy.deepcopy(brand), [a.id for _, a, _ in assets])
        )
        return super().generate(brief, brand, prompt, assets)


class HTTPClient(DirectClient):
    def tool(self, step_id, operation, arguments=None):
        try:
            return super().tool(step_id, operation, arguments)
        except AgentError as error:
            response = httpx.Response(
                error.status,
                json={"code": error.code},
                request=httpx.Request("POST", "https://gateway.example/tools"),
            )
            raise httpx.HTTPStatusError(
                "Tool request failed", request=response.request, response=response
            ) from None


@pytest.fixture
def conversation(agent):
    svc, brand, *_ = agent
    chat = ChatService(svc)
    return chat, chat.create(
        ConversationInput(brand_version=brand["id"], title="Campaign")
    )


@pytest.fixture
def collection(agent, tmp_path):
    """Exercise the legacy read-only snapshot bridge independently of Neon."""
    import hashlib

    from app.models import Association, Generation, Image, ServiceState
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel

    database = tmp_path / "catalog.sqlite3"
    image_root = tmp_path / "collection-images"
    image_root.mkdir()
    data = picture()
    (image_root / "red.png").write_bytes(data)
    engine = create_engine(f"sqlite:///{database}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Generation(
                id="fixture",
                status="ready",
                config_hash="test",
                collection="test",
                model="test",
                dimensions=3,
            )
        )
        session.add(
            Image(
                generation_id="fixture",
                image_id="red",
                relative_path="red.png",
                location="red.png",
                checksum=hashlib.sha256(data).hexdigest(),
                mime_type="image/png",
                width=32,
                height=32,
                title="red",
            )
        )
        session.add(
            Association(
                id="association-red",
                generation_id="fixture",
                image_id="red",
                record_uid="co-red",
                image_uid="i-red",
                source_json="fixture.json",
                title="red",
                licence="CC BY-NC-SA 4.0",
                credit="Fixture credit",
            )
        )
        session.add(ServiceState(active_generation="fixture"))
        session.commit()
    engine.dispose()
    agent[0].settings.collection_database = database
    agent[0].settings.collection_image_root = image_root
    return "red"


def execute(image_id=None, asset_id=None, colors=None, clear_subject=False):
    return {
        "action": "execute",
        "execution": {
            "prompt": "Preserve the selected subject; use the requested campaign style.",
            "image_id": image_id,
            "asset_id": asset_id,
            "clear_subject": clear_subject,
            "overrides": {"colors": colors},
        },
    }


def submit(agent, conversation, provider, text="Create an image", key="one"):
    response = conversation[0].submit(
        conversation[1]["id"], MessageInput(content=text), key
    )
    worker = Worker(agent[0], agent[4], chat_provider=provider)
    worker.tick()
    return response["run"]["id"], worker


def complete(agent, provider, run_id, worker):
    svc = agent[0]
    with Session(svc.engine) as session:
        run = svc.run(session, run_id)
        claims = verify(svc.settings, task_token(svc.settings, run), "task")
    run_agent(HTTPClient(svc, provider, claims))
    assert svc.read_run(run_id)["status"] == "execution_done"
    worker.tick()
    return svc.read_run(run_id)


def test_plain_chat_and_clarification_never_start_a_sandbox(agent, conversation):
    svc = agent[0]
    svc.settings.gateway_url = ""
    provider = ChatProvider(
        [
            {"action": "reply", "message": "Which image and colors would you like?"},
            {"action": "reply", "message": "We can use a blue background."},
        ]
    )
    first, _ = submit(agent, conversation, provider, "Hello")
    assert svc.read_run(first)["status"] == "succeeded"
    assert agent[4].created == 0 and provider.generated == 0
    restarted = ChatService(AgentService(svc.settings, svc.engine))
    submit(agent, (restarted, conversation[1]), provider, "Can we use blue?", key="two")
    assert len(provider.contexts[-1]["messages"]) == 3
    assert len(restarted.read(conversation[1]["id"])["messages"]) == 4
    assert agent[4].created == 0


def test_compiled_task_overrides_brand_then_returns_to_chat(
    agent, conversation, collection
):
    svc, brand, *_ = agent
    with transaction(svc.engine) as session:
        saved = svc.brand(session, brand["id"])
        saved.profile = {**saved.profile, "colors": "red"}
    provider = ChatProvider(
        [
            execute(image_id=collection, colors="blue"),
            {"action": "reply", "message": "Here is the blue version."},
        ]
    )
    run_id, worker = submit(
        agent,
        conversation,
        provider,
        f"Use ID {collection}, but use blue instead of brand red",
    )
    assert agent[4].created == 1
    with Session(svc.engine) as session:
        run = svc.run(session, run_id)
        claims = verify(svc.settings, task_token(svc.settings, run), "task")
    manifest = svc.manifest(claims)
    assert manifest["brand"]["colors"] == "blue"
    assert manifest["execution"]["image_id"] == collection
    assert "messages" not in str(manifest)
    assert manifest["request"] == {"mode": "execution"}
    result = complete(agent, provider, run_id, worker)
    assert result["status"] == "succeeded"
    assert [c["phase"] for c in provider.contexts] == ["prepare", "result"]
    assert provider.inputs[0][1]["colors"] == "blue"
    assert provider.effective_evaluation_brands[0]["colors"] == "blue"
    with Session(svc.engine) as session:
        assert svc.brand(session, brand["id"]).profile["colors"] == "red"
        generated = svc.asset(session, result["artifacts"][0]["id"])
        subject = svc.asset(session, generated.source["reference_ids"][0])
        assert subject.source["image_id"] == collection
        assert subject.source["associations"][0]["credit"] == "Fixture credit"
    history = conversation[0].read(conversation[1]["id"])["messages"]
    assert history[-1]["content"] == "Here is the blue version."
    assert history[-1]["asset_ids"] == [result["artifacts"][0]["id"]]


def test_followup_edit_uses_previous_output_as_subject(agent, conversation):
    provider = ChatProvider(
        [
            execute(),
            {"action": "reply", "message": "First image."},
            execute(colors="green"),
            {"action": "reply", "message": "Edited image."},
        ]
    )
    first, w = submit(agent, conversation, provider)
    output = complete(agent, provider, first, w)["artifacts"][0]["id"]
    second, w = submit(
        agent, conversation, provider, "Make the last image green", key="two"
    )
    complete(agent, provider, second, w)
    assert output in provider.inputs[-1][2]
    assert provider.inputs[-1][1]["colors"] == "green"
    assert len(provider.contexts[2]["messages"]) == 3


def test_start_from_scratch_clears_subject_and_retains_brand_references(
    agent, conversation
):
    fresh = execute(clear_subject=True)
    fresh["execution"]["prompt"] = "Create an unrelated abstract pattern from scratch."
    provider = ChatProvider(
        [
            execute(),
            {"action": "reply", "message": "First image."},
            fresh,
            {"action": "reply", "message": "New design."},
        ]
    )
    first, worker = submit(agent, conversation, provider)
    previous = complete(agent, provider, first, worker)["artifacts"][0]["id"]
    second, worker = submit(
        agent, conversation, provider, fresh["execution"]["prompt"], key="fresh"
    )
    request = agent[0].read_run(second)["request"]
    assert request["subject_asset_ids"] == []
    assert request["execution"]["asset_id"] is None
    assert conversation[0].read(conversation[1]["id"])["subject_asset_id"] is None
    result = complete(agent, provider, second, worker)
    assert previous not in provider.inputs[-1][2]
    assert provider.inputs[-1][2] == agent[1]["reference_asset_ids"]
    history = conversation[0].read(conversation[1]["id"])
    assert previous in {asset["id"] for asset in history["assets"]}
    assert history["subject_asset_id"] == result["artifacts"][0]["id"]


@pytest.mark.parametrize("source", [{"image_id": "co123"}, {"asset_id": "prior"}])
def test_start_from_scratch_rejects_an_explicit_source(source):
    with pytest.raises(ValidationError, match="cannot specify a source"):
        ExecutionRequest(prompt="A new design", clear_subject=True, **source)


@pytest.mark.parametrize("action", ["accept", "revise"])
def test_chat_history_preserves_review_status_after_success(agent, conversation, action):
    class Reviewer(ChatProvider):
        def evaluate(self, brief, brand, assets):
            return {
                "subject_score": 90 if action == "accept" else 30,
                "brand_score": 90 if action == "accept" else 30,
                "request_score": 90 if action == "accept" else 30,
                "summary": "Review result",
                "action": action,
                "revision_prompt": "Improve the composition.",
            }, None

    provider = Reviewer([execute(), {"action": "reply", "message": "Here is your image."}])
    run_id, worker = submit(agent, conversation, provider)
    result = complete(agent, provider, run_id, worker)
    assert result["status"] == "succeeded"
    expected = "accepted" if action == "accept" else "needs_review"
    assert result["review_status"] == expected
    assert provider.generated == (1 if action == "accept" else 2)
    svc = agent[0]
    reopened = ChatService(AgentService(svc.settings, svc.engine))
    message = reopened.read(conversation[1]["id"])["messages"][-1]
    assert message["review_status"] == expected
    assert message["asset_ids"] == [result["artifacts"][0]["id"]]


def test_missing_image_returns_execution_failure_to_chat(
    agent, conversation, collection
):
    provider = ChatProvider(
        [
            execute(image_id="missing"),
            {"action": "reply", "message": "Please check that image ID."},
        ]
    )
    run_id, w = submit(agent, conversation, provider)
    result = complete(agent, provider, run_id, w)
    assert result["status"] == "failed" and provider.generated == 0
    assert provider.contexts[-1]["result"]["execution_status"] == "failed"
    assert (
        conversation[0].read(conversation[1]["id"])["messages"][-1]["content"]
        == "Please check that image ID."
    )


def test_result_chat_failure_preserves_generated_image(agent, conversation):
    provider = ChatProvider([execute()])
    run_id, w = submit(agent, conversation, provider)
    result = complete(agent, provider, run_id, w)
    assert result["status"] == "succeeded" and len(result["artifacts"]) == 1
    assert (
        "summary is unavailable"
        in conversation[0].read(conversation[1]["id"])["messages"][-1]["content"]
    )


def test_sandbox_cannot_chat_or_change_compiled_source(agent, conversation, collection):
    provider = ChatProvider([execute(image_id=collection)])
    run_id, _ = submit(agent, conversation, provider)
    svc = agent[0]
    with Session(svc.engine) as session:
        run = svc.run(session, run_id)
        claims = verify(svc.settings, task_token(svc.settings, run), "task")
    with pytest.raises(ValidationError):
        ToolRequest(step_id="chat", operation="chat_decide")
    direct = DirectClient(svc, provider, claims)
    with pytest.raises(AgentError) as error:
        direct.tool("wrong_source", "execution_image", {"image_id": "another"})
    assert error.value.status == 422
    with pytest.raises(AgentError) as error:
        direct.tool("premature", "generate", {"prompt": "Wrong"})
    assert error.value.code == "source_missing"
    with pytest.raises(AgentError) as error:
        direct.tool("plan", "plan")
    assert error.value.status == 403
    assert provider.generated == 0


def test_chat_idempotency_concurrency_and_cancel_before_execution(agent, conversation):
    chat, row = conversation
    body = MessageInput(content="Generate")
    first = chat.submit(row["id"], body, "same")
    assert chat.submit(row["id"], body, "same") == first
    with pytest.raises(AgentError) as error:
        chat.submit(row["id"], MessageInput(content="Different"), "same")
    assert error.value.code == "idempotency_conflict"

    def send(key):
        try:
            chat.submit(row["id"], body, key)
        except AgentError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(send, ["two", "three"])) == [
            "conversation_busy",
            "conversation_busy",
        ]
    agent[0].cancel(first["run"]["id"])
    provider = ChatProvider([])
    Worker(agent[0], agent[4], provider).tick()
    assert provider.contexts == [] and agent[4].created == 0
    assert chat.read(row["id"])["messages"][-1]["run_status"] == "cancelled"


def test_late_planner_result_is_fenced_after_cancellation(agent, conversation):
    svc = agent[0]
    response = conversation[0].submit(
        conversation[1]["id"], MessageInput(content="Generate"), "one"
    )

    class Cancelling(ChatProvider):
        def chat(self, *args):
            svc.cancel(response["run"]["id"])
            return execute(), None

    worker = Worker(svc, agent[4], Cancelling([]))
    worker.tick()
    assert svc.read_run(response["run"]["id"])["status"] == "cancelled"
    assert agent[4].created == 0
    with Session(svc.engine) as session:
        assert (
            session.scalar(
                select(Call).where(Call.run_id == response["run"]["id"])
            ).status
            == "completed"
        )


def test_unknown_chat_submission_after_crash_is_not_replayed(agent, conversation):
    svc = agent[0]
    response = conversation[0].submit(
        conversation[1]["id"], MessageInput(content="Generate"), "one"
    )
    with transaction(svc.engine) as session:
        run = svc.run(session, response["run"]["id"])
        run.status = "chat_preparing"
        run.lease_until = time.time() - 1
        session.add(
            Call(
                run_id=run.id,
                step_id="chat_prepare",
                operation="chat_prepare",
                request_hash="x",
            )
        )
    provider = ChatProvider([])
    Worker(svc, agent[4], provider).tick()
    assert provider.contexts == [] and agent[4].created == 0
    assert svc.read_run(response["run"]["id"])["error_code"] == "chat_outcome_unknown"


def test_workspace_and_explicit_asset_scope(agent, conversation):
    svc = agent[0]
    other = ChatService(
        AgentService(svc.settings.model_copy(update={"workspace": "other"}), svc.engine)
    )
    with pytest.raises(AgentError) as error:
        other.read(conversation[1]["id"])
    assert error.value.status == 404
    asset = svc.upload(picture(), "subject")
    provider = ChatProvider([execute(asset_id=asset["id"])])
    run_id, _ = submit(agent, conversation, provider)
    assert svc.read_run(run_id)["error_code"] == "invalid_reference"
    assert agent[4].created == 0


def test_collection_file_integrity_and_path_scope(agent, collection):
    import sqlite3

    svc = agent[0]
    (svc.settings.collection_image_root / "red.png").write_bytes(picture("blue"))
    with pytest.raises(AgentError) as error:
        read_collection_image(svc.settings, collection)
    assert error.value.code == "image_changed"
    with sqlite3.connect(svc.settings.collection_database) as db:
        db.execute(
            "UPDATE images SET relative_path=? WHERE image_id=?",
            ("../outside.png", collection),
        )
    with pytest.raises(AgentError) as error:
        read_collection_image(svc.settings, collection)
    assert error.value.code == "invalid_collection_path"


def test_online_lookup_uses_fixed_endpoints_not_metadata_urls(agent, monkeypatch):
    svc = agent[0]
    svc.settings.collection_api_url = "https://collection.example"
    original = httpx.Client
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/file"):
            return httpx.Response(200, content=picture())
        return httpx.Response(
            200,
            json={
                "image_id": "id-123",
                "title": "Photo",
                "image_url": "https://evil.example/private",
                "associations": [{"credit": "Museum", "licence": "CC0"}],
            },
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(**kw, transport=httpx.MockTransport(handle)),
    )
    data, source = read_collection_image(svc.settings, "id-123")
    assert data == picture()
    assert [str(req.url) for req in requests] == [
        "https://collection.example/api/v1/images/id-123",
        "https://collection.example/api/v1/images/id-123/file",
    ]
    assert source["associations"][0]["credit"] == "Museum"


def test_online_lookup_rejects_redirects(agent, monkeypatch):
    svc = agent[0]
    svc.settings.collection_api_url = "https://collection.example"
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(
            **kw,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    302, headers={"Location": "https://elsewhere.example"}
                )
            ),
        ),
    )
    with pytest.raises(AgentError) as error:
        read_collection_image(svc.settings, "one")
    assert error.value.code == "collection_unavailable"


def test_chat_api_and_progress(agent):
    svc, brand, *_ = agent
    with TestClient(create_agent_app(svc.settings)) as client:
        assert (
            client.post(
                "/api/v1/agent/conversations", json={"brand_version": brand["id"]}
            ).status_code
            == 401
        )
        client.headers["Authorization"] = (
            "Bearer " + svc.settings.access_token.get_secret_value()
        )
        cid = client.post(
            "/api/v1/agent/conversations", json={"brand_version": brand["id"]}
        ).json()["id"]
        response = client.post(
            f"/api/v1/agent/conversations/{cid}/messages",
            json={"content": "Hello"},
            headers={"Idempotency-Key": "one"},
        )
        assert response.status_code == 202
        assert response.json()["run"]["status"] == "chat_queued"
        run_id = response.json()["run"]["id"]
        Worker(
            client.app.state.agent,
            agent[4],
            ChatProvider([{"action": "reply", "message": "Hello!"}]),
        ).tick()
        assert (
            "assistant_message"
            in client.get(f"/api/v1/agent/runs/{run_id}/events").text
        )
        assert (
            client.get(f"/api/v1/agent/conversations/{cid}").json()["messages"][-1][
                "content"
            ]
            == "Hello!"
        )


def test_chat_migration_upgrades_existing_agent_tables_without_data_loss(tmp_path):
    from alembic import command
    from alembic.config import Config
    from app.agent_models import Brand
    from app.core.config import ROOT
    from app.services.agent.config import AgentSettings
    from app.services.agent.db import engine_for, migrate, transaction

    settings = AgentSettings(
        _env_file=None, database_url=f"sqlite:///{tmp_path}/old.sqlite3"
    )
    engine = engine_for(settings)
    config = Config()
    config.set_main_option("script_location", str(ROOT / "backend/app/agent_alembic"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "agent_0001")
    with transaction(engine) as session:
        session.add(
            Brand(
                id="old-brand",
                workspace="default",
                brand_id="old",
                version=1,
                profile={"name": "Existing"},
            )
        )
    migrate(engine, "default")
    with Session(engine) as session:
        assert session.get(Brand, "old-brand").profile["name"] == "Existing"
        from app.agent_models import Conversation

        assert session.scalars(select(Conversation)).all() == []
    engine.dispose()


def test_executor_can_revise_without_calling_chat_inside_sandbox(agent, conversation):
    provider = ChatProvider(
        [execute(colors="blue"), {"action": "reply", "message": "Revised result."}]
    )
    provider.evaluated = 0
    run_id, worker = submit(agent, conversation, provider)
    result = complete(agent, provider, run_id, worker)
    assert provider.generated == 2
    assert [c["phase"] for c in provider.contexts] == ["prepare", "result"]
    assert all(brand["colors"] == "blue" for _, brand, _ in provider.inputs)
    assert result["status"] == "succeeded"


def test_online_source_does_not_escape_path_or_accept_large_payload(agent, monkeypatch):
    svc = agent[0]
    svc.settings.collection_api_url = "https://collection.example"
    original = httpx.Client
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/file"):
            return httpx.Response(200, content=b"x" * 11)
        return httpx.Response(200, json={"image_id": "one", "associations": []})

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(**kw, transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(AgentError):
        read_collection_image(svc.settings, "..")
    assert requests == []
    svc.settings.max_image_bytes = 10
    with pytest.raises(AgentError) as error:
        read_collection_image(svc.settings, "one")
    assert error.value.code == "image_too_large"


def test_collection_record_lookup_returns_distinct_scoped_matches(agent, collection):
    from app.models import Association, Generation, Image
    from app.services.agent.collection import resolve_record_images
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{agent[0].settings.collection_database}")
    with Session(engine) as db:
        row = db.get(Association, "association-red")
        row.record_uid = "co41679"
        db.add(
            Association(
                id="duplicate",
                generation_id="fixture",
                image_id=collection,
                record_uid="co41679",
                source_json="duplicate.json",
            )
        )
        db.add(
            Generation(
                id="old",
                collection="old",
                status="ready",
                model="test",
                dimensions=3,
                config_hash="test",
            )
        )
        db.add(
            Image(
                generation_id="old",
                image_id="old-image",
                location="old.png",
                relative_path="old.png",
                checksum="old",
                mime_type="image/png",
                width=1,
                height=1,
                title="old",
            )
        )
        db.add(
            Association(
                id="old-association",
                generation_id="old",
                image_id="old-image",
                record_uid="co41679",
                source_json="old.json",
            )
        )
        db.commit()
    rows = resolve_record_images("co41679", engine)
    assert [r["image_id"] for r in rows] == [collection]
    assert rows[0]["associations"][0]["licence"] == "CC BY-NC-SA 4.0"
    assert resolve_record_images("co41679' OR 1=1 --", engine) == []
    with Session(engine) as db:
        from app.models import Generation

        db.get(Generation, "fixture").status = "building"
        db.commit()
    with pytest.raises(AgentError) as error:
        resolve_record_images("co41679", engine)
    assert error.value.code == "collection_unavailable"
    engine.dispose()


def test_record_resolves_to_uuid_before_online_fetch(agent, monkeypatch):
    import app.services.agent.collection as lookup

    svc = agent[0]
    svc.settings.collection_api_url = "https://collection.example"
    matches = [{"image_id": "resolved-uuid", "title": "Boat"}]
    monkeypatch.setattr(lookup, "resolve_record_images", lambda record: matches)
    seen = []

    def online(settings, image_id):
        seen.append(image_id)
        return picture(), {"image_id": image_id, "associations": []}

    monkeypatch.setattr(lookup, "read_online_image", online)
    data, source = lookup.read_collection_image(svc.settings, "co41679")
    assert data == picture()
    assert seen == ["resolved-uuid"]
    assert source["requested_record_uid"] == "co41679"
    matches.append({"image_id": "another-uuid", "title": "Another angle"})
    with pytest.raises(AgentError) as error:
        lookup.read_collection_image(svc.settings, "co41679")
    assert error.value.code == "collection_record_ambiguous"
    assert (
        "resolved-uuid" in error.value.message and "another-uuid" in error.value.message
    )
    matches.clear()
    with pytest.raises(AgentError) as error:
        lookup.read_collection_image(svc.settings, "co41679")
    assert error.value.code == "collection_record_missing"
    assert seen == ["resolved-uuid"]


def test_evaluation_rejection_preserves_image_and_safe_diagnostics(
    agent, conversation, caplog
):
    from openai import NotFoundError

    class UnavailableEvaluator(ChatProvider):
        def evaluate(self, brief, brand, assets):
            raise NotFoundError(
                "private-provider-message",
                response=httpx.Response(
                    404,
                    request=httpx.Request(
                        "POST", "https://provider.test/v1/chat/completions"
                    ),
                ),
                body=None,
            )

    provider = UnavailableEvaluator(
        [
            execute(),
            {"action": "reply", "message": "Image saved; evaluation unavailable."},
        ]
    )
    run_id, worker = submit(agent, conversation, provider)
    svc = agent[0]
    with Session(svc.engine) as session:
        claims = verify(
            svc.settings, task_token(svc.settings, svc.run(session, run_id)), "task"
        )
    with pytest.raises(httpx.HTTPStatusError):
        run_agent(HTTPClient(svc, provider, claims))
    failed = svc.read_run(run_id)
    assert failed["error_code"] == "provider_model_unavailable"
    with Session(svc.engine) as session:
        failed["result"] = svc.run(session, run_id).result
    assert failed["result"]["failed_operation"] == "evaluate"
    assert failed["result"]["diagnostic"]["http_status"] == 404
    assert len(failed["result"]["asset_ids"]) == 1
    assert failed["result"]["review_status"] == "needs_review"
    worker.tick()
    history = conversation[0].read(conversation[1]["id"])
    assert history["messages"][-1]["asset_ids"] == failed["result"]["asset_ids"]
    assert history["messages"][-1]["review_status"] == "needs_review"
    assert provider.contexts[-1]["result"]["failed_operation"] == "evaluate"
    assert provider.generated == 1
    assert "private-provider-message" not in caplog.text
    assert "provider_model_unavailable" in caplog.text


def test_record_lookup_reports_building_separately_from_connection_failure(
    agent, collection
):
    from app.models import Generation
    from app.services.agent.collection import resolve_record_images
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{agent[0].settings.collection_database}")
    try:
        with Session(engine) as db:
            db.get(Generation, "fixture").status = "building"
            db.commit()
        with pytest.raises(AgentError) as error:
            resolve_record_images("co41679", engine)
        assert error.value.code == "collection_unavailable"
        assert "awaiting final indexing checks" in str(error.value)
    finally:
        engine.dispose()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_online_image_reader_rejects_redirects_without_forwarding_credentials(
    agent, monkeypatch, status
):
    from pydantic import SecretStr

    svc = agent[0]
    svc.settings.collection_api_url = "https://collection.example"
    svc.settings.collection_api_token = SecretStr("private-collection-token")
    requests = []
    original = httpx.Client

    def handle(request):
        requests.append(request)
        assert request.url.host == "collection.example"
        if request.url.path.endswith("/file"):
            return httpx.Response(
                status, headers={"location": "https://storage.example/image"}
            )
        return httpx.Response(200, json={"image_id": "one", "associations": []})

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(AgentError) as error:
        read_collection_image(svc.settings, "one")
    assert error.value.code == "collection_unavailable"
    assert len(requests) == 2
