from types import SimpleNamespace

import pytest
from app import main, studio
from app.core.config import Settings
from app.services.agent import application, db
from app.services.agent.config import AgentSettings
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize("failure", ["vectors", "agent", "shutdown"])
def test_search_lifespan_releases_acquired_resources(monkeypatch, failure):
    closed = []

    def fail():
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(main, "configure_telemetry", lambda *args: None)
    monkeypatch.setattr(
        main.logfire, "force_flush", lambda **kwargs: closed.append("flush")
    )
    monkeypatch.setattr(
        main,
        "make_engine",
        lambda _: SimpleNamespace(dispose=lambda: closed.append("engine")),
    )
    monkeypatch.setattr(
        main,
        "VectorStore",
        lambda _: (
            fail()
            if failure == "vectors"
            else SimpleNamespace(close=lambda: closed.append("vectors"))
        ),
    )
    monkeypatch.setattr(
        main,
        "create_embeddings",
        lambda _: SimpleNamespace(close=lambda: closed.append("embeddings")),
    )
    monkeypatch.setattr(
        application, "start", lambda _: fail() if failure == "agent" else None
    )

    def stop(_):
        closed.append("agent")
        if failure == "shutdown":
            fail()

    monkeypatch.setattr(application, "stop", stop)
    with (
        pytest.raises(RuntimeError, match="fixture failure"),
        TestClient(studio.create_app(Settings(_env_file=None))),
    ):
        pass
    expected = ["engine", "flush"]
    if failure in {"agent", "shutdown"}:
        expected = ["embeddings", "vectors", *expected]
    if failure == "shutdown":
        expected.insert(0, "agent")
    assert closed == expected


def test_agent_migration_failure_disposes_engine(monkeypatch):
    disposed = []
    monkeypatch.setattr(
        db,
        "engine_for",
        lambda _: SimpleNamespace(dispose=lambda: disposed.append(True)),
    )

    def fail(*args):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(db, "migrate", fail)
    app = FastAPI()
    with pytest.raises(RuntimeError, match="migration failed"):
        application.start(
            app, AgentSettings(_env_file=None, enabled=True, access_token="x" * 32)
        )
    assert disposed == [True]
    assert app.state.agent is None
