"""Exercise async supervision with real transactions and deliberately stalled I/O."""

import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
from app.core.config import Settings
from app.explore import auth
from app.explore.concurrency import blocking
from app.explore.config import ExplorerSettings
from app.explore.contracts import Message
from app.explore.models import Base, Conversation, Run
from app.explore.routes import router
from app.explore.service import Explorer
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def explorer(tmp_path):
    # File SQLite exercises transaction/thread lifetimes without a network DB.
    # PostgreSQL ownership and row-lock behavior remain in test_explorer.py.
    engine = create_engine(f"sqlite:///{tmp_path / 'explorer.sqlite3'}")
    Base.metadata.create_all(engine)
    options = ExplorerSettings(
        _env_file=None,
        api_key="fixture",
        model="fixture",
        state_dir=tmp_path,
    )

    async def runner(payload, accept):
        await accept({"kind": "thread", "id": "fixture-thread"})
        await accept(
            {"kind": "result", "result": {"answer": "Fixture answer", "image_ids": []}}
        )

    service = Explorer(
        SimpleNamespace(engine=engine, settings=Settings(_env_file=None)),
        options,
        runner=runner,
    )
    yield service
    engine.dispose()


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


def stall(monkeypatch, instance, method, *, call_number=1):
    entered, release = threading.Event(), threading.Event()
    original = getattr(instance, method)
    count = 0

    def delayed(*args, **kwargs):
        nonlocal count
        count += 1
        if count == call_number:
            entered.set()
            assert release.wait(3), "The event loop could not release the stalled I/O"
        return original(*args, **kwargs)

    monkeypatch.setattr(instance, method, delayed)
    return entered, release


@pytest.mark.parametrize("call_number", [1, 2])
def test_sse_database_reads_allow_other_requests(explorer, monkeypatch, call_number):
    conversation = explorer.create("alice", "Cameras")["id"]
    run, _ = explorer.save_run(
        "alice", conversation, Message(content="Find cameras"), "one"
    )
    explorer.fail(run["id"], "cancelled", "cancelled")
    entered, release = stall(monkeypatch, explorer, "events", call_number=call_number)
    app = FastAPI()
    app.state.explorer = explorer
    app.dependency_overrides[auth.principal] = lambda: "alice"
    app.include_router(router, prefix="/api/v1")

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            events = asyncio.create_task(
                client.get(f"/api/v1/explorer/runs/{run['id']}/events")
            )
            try:
                await until(entered.is_set)
                status = await asyncio.wait_for(
                    client.get("/api/v1/explorer/status"), 1
                )
                assert status.status_code == 200
                assert not events.done()
            finally:
                release.set()
                response = await events
            assert "event: status" in response.text
            await explorer.close()

    asyncio.run(check())


@pytest.mark.parametrize("phase", ["prepare_run", "accept", "clear_active_run"])
def test_run_transactions_allow_other_tasks(explorer, monkeypatch, phase):
    conversation = explorer.create("alice", "Cameras")["id"]
    entered, release = stall(monkeypatch, explorer, phase)

    async def check():
        try:
            result = await explorer.submit(
                "alice", conversation, Message(content="Find cameras"), "one"
            )
            await until(entered.is_set)
            # The supervisor is still processing its transaction while unrelated
            # event-loop work can read the durable conversation in another thread.
            history = await asyncio.wait_for(
                blocking(explorer.history, "alice", conversation), 1
            )
            assert history["runs"][0]["id"] == result["id"]
            assert explorer.tasks
        finally:
            release.set()
            await until(lambda: not explorer.tasks)
            await explorer.close()
        history = await blocking(explorer.history, "alice", conversation)
        assert history["runs"][0]["status"] == "succeeded"

    asyncio.run(check())


def test_cancelled_http_submission_still_dispatches_once(explorer, monkeypatch):
    conversation = explorer.create("alice", "Cameras")["id"]
    entered, release = stall(monkeypatch, explorer, "save_run")

    async def check():
        body = Message(content="Find cameras")
        submission = asyncio.create_task(
            explorer.submit("alice", conversation, body, "one")
        )
        try:
            await until(entered.is_set)
            submission.cancel()
            await asyncio.sleep(0)
            assert not submission.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await submission
        await until(lambda: not explorer.tasks)
        retried = await explorer.submit("alice", conversation, body, "one")
        assert retried["status"] == "succeeded"
        assert not explorer.tasks
        await explorer.close()
        with Session(explorer.engine) as session:
            assert len(session.scalars(select(Run)).all()) == 1
            assert session.get(Conversation, conversation).active_run is None

    asyncio.run(check())


def test_cancellation_waits_for_started_transaction_before_cleanup(
    explorer, monkeypatch
):
    conversation = explorer.create("alice", "Cameras")["id"]
    entered, release = stall(monkeypatch, explorer, "prepare_run")

    async def check():
        result = await explorer.submit(
            "alice", conversation, Message(content="Find cameras"), "one"
        )
        try:
            await until(entered.is_set)
            await explorer.cancel("alice", result["id"])
            assert explorer.tasks
            # A second cancellation must not abandon the first transaction.
            explorer.tasks[result["id"]].cancel()
            await asyncio.sleep(0)
            assert explorer.tasks
        finally:
            release.set()
            await explorer.close()
        with Session(explorer.engine) as session:
            assert session.get(Run, result["id"]).status == "cancelled"
            assert session.get(Conversation, conversation).active_run is None
        assert not explorer.tasks
        assert not explorer.cleanups

    asyncio.run(check())


def test_shutdown_during_admission_does_not_launch_work(explorer, monkeypatch):
    conversation = explorer.create("alice", "Cameras")["id"]
    entered, release = stall(monkeypatch, explorer, "save_run")
    launched = []

    async def runner(payload, accept):
        launched.append(payload)

    explorer.runner = runner

    async def check():
        submission = asyncio.create_task(
            explorer.submit(
                "alice", conversation, Message(content="Find cameras"), "one"
            )
        )
        await until(entered.is_set)
        shutdown = asyncio.create_task(explorer.close())
        try:
            await until(lambda: explorer.stopping)
            assert not shutdown.done()
        finally:
            release.set()
        with pytest.raises(HTTPException) as failure:
            await submission
        assert failure.value.status_code == 503
        await shutdown
        assert not launched
        with Session(explorer.engine) as session:
            assert session.get(Conversation, conversation).active_run is None
            assert session.scalar(select(Run)).status == "failed"

    asyncio.run(check())
