import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.main import create_app
from app.services import search
from app.services.deadline import deadline_lock, search_deadline
from app.services.embeddings import SearchError
from app.services.ingestion import run_ingestion
from fastapi.testclient import TestClient


@pytest.mark.parametrize("kind", ["text", "image", "similar"])
def test_deadline_returns_504_and_stops_later_work(setup, monkeypatch, kind):
    settings, engine, selected, _, generation, vectors, embeddings = setup
    run_ingestion(engine, settings, generation, embeddings, vectors)
    release = threading.Event()
    started = threading.Event()
    queried = []

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return [1.0, 0.0, 0.0]

    app = create_app(settings, vectors=vectors, embeddings=embeddings)
    with TestClient(app) as client:
        assert client.get("/api/v1/status").json()["status"] == "ready"
        monkeypatch.setattr(search, "SEARCH_TIMEOUT_SECONDS", 0.1)
        monkeypatch.setattr(
            vectors if kind == "similar" else embeddings,
            "vector" if kind == "similar" else "embed",
            blocked,
        )
        monkeypatch.setattr(vectors, "search", lambda *a, **k: queried.append(True))
        try:
            before = time.monotonic()
            if kind == "image":
                response = client.post(
                    "/api/v1/search/image",
                    files={
                        "image": (
                            "red.png",
                            (settings.image_root / "red.png").read_bytes(),
                            "image/png",
                        )
                    },
                )
            elif kind == "similar":
                response = client.post(
                    f"/api/v1/images/{selected[0]['image_id']}/similar"
                )
            else:
                response = client.post("/api/v1/search/text", json={"query": "red"})
            assert started.is_set()
            assert response.status_code == 504
            assert response.json()["code"] == "search_timeout"
            assert time.monotonic() - before < 1
        finally:
            release.set()
    # Closing the API drains the underlying job before shared clients close.
    assert not queried


def test_expired_jobs_retain_capacity_until_they_finish(setup, monkeypatch):
    settings, engine, _, _, generation, vectors, embeddings = setup
    run_ingestion(engine, settings, generation, embeddings, vectors)
    release = threading.Event()
    calls = []

    def blocked(**kwargs):
        calls.append(True)
        assert release.wait(5)
        return [1.0, 0.0, 0.0]

    with TestClient(
        create_app(settings, vectors=vectors, embeddings=embeddings)
    ) as client:
        assert client.get("/api/v1/status").json()["status"] == "ready"
        monkeypatch.setattr(search, "SEARCH_TIMEOUT_SECONDS", 0.1)
        monkeypatch.setattr(embeddings, "embed", blocked)
        try:
            for _ in range(4):
                assert (
                    client.post(
                        "/api/v1/search/text", json={"query": "red"}
                    ).status_code
                    == 504
                )
            response = client.post("/api/v1/search/text", json={"query": "red"})
            assert response.status_code == 429
            assert response.json()["code"] == "search_busy"
            assert len(calls) == 4
        finally:
            release.set()


def test_expired_lock_wait_never_enters_inference():
    lock = threading.Lock()
    with lock, ThreadPoolExecutor(max_workers=1) as pool:

        def waiter():
            with search_deadline(time.monotonic() + 0.05), deadline_lock(lock):
                pytest.fail("An expired search entered inference")

        with pytest.raises(SearchError) as error:
            pool.submit(waiter).result(timeout=1)
        assert error.value.code == "search_timeout"
