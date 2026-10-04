import asyncio
import time

import pytest
from app.explore.auth import COOKIE, sign
from app.explore.config import ExplorerSettings
from app.explore.contracts import ToolCall
from app.explore.models import Conversation, Run
from app.services.ingestion import run_ingestion
from app.web import create_app
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def explorer(setup, tmp_path):
    settings, engine, selected, _, generation, vectors, embeddings = setup
    run_ingestion(engine, settings, generation, embeddings, vectors)
    options = ExplorerSettings(
        _env_file=None,
        api_key="fixture",
        model="fixture",
        session_secret="s" * 48,
        oauth_client_id="fixture",
        state_dir=tmp_path / "state",
    )
    state = {"wait": False, "payloads": [], "bad_reference": False}

    async def runner(payload, accept):
        state["payloads"].append(payload)
        while state["wait"]:
            await asyncio.sleep(0.02)
        accept({"kind": "thread", "id": "thread-fixture"})
        service = app.state.explorer
        args = (
            {"upload_id": payload["upload_id"]}
            if payload["upload_id"]
            else {"query": "red"}
        )
        result = await asyncio.to_thread(
            service.tool,
            payload["run_id"],
            payload["tool_token"],
            ToolCall(
                name="search_image" if payload["upload_id"] else "search_text",
                arguments=args,
            ),
        )
        ids = [result["result"]["results"][0]["image_id"]]
        accept(
            {
                "kind": "result",
                "result": {
                    "answer": "These results match the collection evidence.",
                    "image_ids": ["made-up"] if state["bad_reference"] else ids,
                },
                "usage": {"input_tokens": 10},
            }
        )

    app = create_app(
        settings,
        explorer_settings=options,
        vectors=vectors,
        embeddings=embeddings,
        runner=runner,
    )
    with TestClient(app, headers={"Origin": options.public_url}) as client:
        client.cookies.set(COOKIE, sign(options, {"sub": "alice"}, "explorer", 3600))
        yield client, options, state, app, selected


def new(client):
    response = client.post("/api/v1/explorer/conversations", json={"title": "Cameras"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def send(client, conversation, key="one", **body):
    return client.post(
        f"/api/v1/explorer/conversations/{conversation}/messages",
        headers={"Idempotency-Key": key},
        json={"content": "Find red objects", **body},
    )


def history(client, conversation, terminal=True):
    deadline = time.monotonic() + 5
    while True:
        result = client.get(f"/api/v1/explorer/conversations/{conversation}").json()
        if not terminal or (
            result["runs"]
            and result["runs"][-1]["status"]
            in {"succeeded", "failed", "cancelled", "timed_out"}
        ):
            return result
        assert time.monotonic() < deadline, result
        time.sleep(0.02)


def test_user_owned_conversations_results_sse_and_idempotency(explorer):
    client, options, state, _app, _ = explorer
    conversation = new(client)
    response = send(client, conversation)
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    run = history(client, conversation)["runs"][0]
    assert run["status"] == "succeeded", run
    assert run["results"][0]["title"] == "red"
    assert run["results"][0]["associations"][0]["credit"] == "Fixture credit"
    assert send(client, conversation).json()["id"] == run_id
    assert len(state["payloads"]) == 1
    assert send(client, conversation, content="different").status_code == 409
    events = client.get(f"/api/v1/explorer/runs/{run_id}/events").text
    assert "event: answer" in events
    last_id = max(
        int(line[4:]) for line in events.splitlines() if line.startswith("id: ")
    )
    assert (
        client.get(
            f"/api/v1/explorer/runs/{run_id}/events",
            headers={"Last-Event-ID": str(last_id)},
        ).text
        == ""
    )
    token = state["payloads"][0]["tool_token"]
    assert (
        client.post(
            f"/api/v1/explorer/internal/{run_id}/tool",
            headers={"Authorization": "Bearer " + token},
            json={"name": "get_filter_options"},
        ).status_code
        == 403
    )
    client.cookies.set(COOKIE, sign(options, {"sub": "bob"}, "explorer", 3600))
    assert client.get("/api/v1/explorer/conversations").json() == []
    assert (
        client.get(f"/api/v1/explorer/conversations/{conversation}").status_code == 404
    )
    assert client.get(f"/api/v1/explorer/runs/{run_id}/events").status_code == 404
    assert client.post(f"/api/v1/explorer/runs/{run_id}/cancel").status_code == 404
    assert send(client, conversation).status_code == 404
    assert (
        client.post(
            "/api/v1/explorer/conversations",
            json={},
            headers={"Origin": "https://attacker.test"},
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert client.get("/api/v1/explorer/conversations").status_code == 401
    assert client.get("/api/v1/agent/status").status_code == 404
    assert client.get("/create").status_code == 404


def test_upload_ownership_and_image_search(explorer):
    client, options, _, app, selected = explorer
    data = (
        app.state.search.settings.image_root / selected[1]["relative_path"]
    ).read_bytes()
    upload = client.post(
        "/api/v1/explorer/uploads", files={"image": ("x.png", data, "image/png")}
    )
    assert upload.status_code == 201, upload.text
    id = upload.json()["id"]
    client.cookies.set(COOKIE, sign(options, {"sub": "bob"}, "explorer", 3600))
    assert send(client, new(client), upload_id=id).status_code == 404
    client.cookies.set(COOKIE, sign(options, {"sub": "alice"}, "explorer", 3600))
    conversation = new(client)
    assert send(client, conversation, upload_id=id).status_code == 202
    run = history(client, conversation)["runs"][0]
    assert run["results"][0]["title"] == "green"
    assert (
        client.post(
            "/api/v1/explorer/uploads", files={"image": ("x.png", b"bad", "image/png")}
        ).status_code
        == 422
    )


def test_cancel_busy_and_untrusted_result_references(explorer):
    client, _, state, app, _ = explorer
    state["wait"] = True
    conversation = new(client)
    run = send(client, conversation).json()
    assert send(client, conversation, key="two").status_code == 409
    assert client.post(f"/api/v1/explorer/runs/{run['id']}/cancel").status_code == 200
    assert history(client, conversation)["runs"][0]["status"] == "cancelled"
    deadline = time.monotonic() + 5
    while app.state.explorer.tasks:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    state.update(wait=False, bad_reference=True)
    assert send(client, conversation, key="three").status_code == 202
    result = history(client, conversation)["runs"][-1]
    assert result["error"] == "invalid_result_reference"
    assert result["results"] == []
    with Session(app.state.explorer.engine) as session:
        assert session.get(Conversation, conversation).thread_id == "thread-fixture"
        assert session.get(Run, result["id"]).token_hash is None


def test_tool_validation_budget_expiry_and_catalogue_evidence(explorer):
    client, options, state, app, selected = explorer
    from app.models import Association
    from sqlalchemy import update

    expected_ids = {selected[0]["image_id"], selected[2]["image_id"]}
    with Session(app.state.explorer.engine) as session, session.begin():
        session.execute(
            update(Association)
            .where(Association.image_id.in_(expected_ids))
            .values(record_uid="co123")
        )
    state["wait"] = True
    conversation = new(client)
    run = send(client, conversation).json()
    deadline = time.monotonic() + 5
    while not state["payloads"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    payload = state["payloads"][0]
    endpoint = f"/api/v1/explorer/internal/{run['id']}/tool"
    headers = {"Authorization": "Bearer " + payload["tool_token"]}

    def tool(name, **arguments):
        return client.post(
            endpoint, headers=headers, json={"name": name, "arguments": arguments}
        )

    assert tool("search_text", query="red", limit=1000).status_code == 422
    assert tool("lookup_record", record_uid="co1' OR 1=1").status_code == 422
    assert tool("get_filter_options").status_code == 200
    result = tool("search_text", query="red", filters={"place": ["London"]})
    assert result.status_code == 200, result.text
    # Discover a real fixture record, then check exact lookup and bounded preview.
    item = tool("search_text", query="red").json()["result"]["results"][0]
    record = item["associations"][0]["record_uid"]
    looked_up = tool("lookup_record", record_uid=record)
    assert looked_up.status_code == 200, looked_up.text
    assert {
        x["image_id"] for x in looked_up.json()["result"]["results"]
    } == expected_ids
    assert (
        tool("lookup_record", record_uid="co999999999").json()["result"]["results"]
        == []
    )
    details = tool("get_image_details", image_id=item["image_id"])
    assert details.json()["preview"]["mimeType"] == "image/jpeg"
    with Session(app.state.explorer.engine) as session, session.begin():
        row = session.get(Run, run["id"])
        row.index_version = "replaced-generation"
    assert tool("get_filter_options").status_code == 409
    with Session(app.state.explorer.engine) as session, session.begin():
        row = session.get(Run, run["id"])
        row.tool_calls = options.max_tool_calls
    assert tool("get_filter_options").status_code == 429
    with Session(app.state.explorer.engine) as session, session.begin():
        session.get(Run, run["id"]).deadline = time.time() - 1
    assert tool("get_filter_options").status_code == 403
    client.post(f"/api/v1/explorer/runs/{run['id']}/cancel")


def test_recovery_fails_uncertain_work_without_replaying(explorer):
    from app.explore.service import Explorer

    client, options, state, app, _ = explorer
    conversation = new(client)
    service = app.state.explorer
    second = Explorer(service.search, options)
    with pytest.raises(RuntimeError, match="Only one"):
        second.start()
    with Session(service.engine) as session, session.begin():
        run = Run(
            conversation_id=conversation,
            request_key="crashed",
            fingerprint="fixture",
            content="Do not replay",
            status="running",
            token_hash="old",
        )
        session.add(run)
        session.flush()
        session.get(Conversation, conversation).active_run = run.id
    service.release_lock()
    second.start()
    try:
        restored = second.history("alice", conversation)["runs"][0]
        assert restored["status"] == "failed"
        assert restored["error"] == "interrupted"
        assert state["payloads"] == []
        with Session(service.engine) as session:
            assert session.get(Conversation, conversation).active_run is None
            assert session.get(Run, restored["id"]).token_hash is None
    finally:
        asyncio.run(second.close())
