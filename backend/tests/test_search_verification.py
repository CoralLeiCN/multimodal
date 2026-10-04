import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from app.main import create_app
from app.services import catalogue
from app.services import search as search_module
from app.services.embeddings import SearchError
from app.services.ingestion import create_generation, run_ingestion
from app.services.search import SearchService
from fastapi.testclient import TestClient
from qdrant_client import models
from sqlalchemy import event


def test_verification_streams_catalogue_into_bounded_vector_reads(setup, monkeypatch):
    settings, engine, selected, report, _, vectors, embeddings = setup
    sample = [dict(selected[0], image_id=str(UUID(int=n + 1))) for n in range(130)]
    generation = create_generation(engine, settings, sample, report)
    run_ingestion(engine, settings, generation, embeddings, vectors)
    monkeypatch.setattr(catalogue, "CATALOGUE_PAGE_SIZE", 64)
    operations = []

    def record_sql(_connection, _cursor, statement, *_args):
        if statement.startswith("SELECT") and "FROM images" in statement:
            operations.append("page")

    retrieve = vectors.client.retrieve

    def record_vectors(collection, ids, **kwargs):
        assert len(ids) <= 64
        operations.append("vectors")
        return retrieve(collection, ids, **kwargs)

    monkeypatch.setattr(vectors.client, "retrieve", record_vectors)
    service = SearchService(engine, settings, vectors, embeddings)
    event.listen(engine, "before_cursor_execute", record_sql)
    try:
        assert service.status().status == "ready"
        assert operations == ["page", "vectors"] * 3
    finally:
        event.remove(engine, "before_cursor_execute", record_sql)
        service.close()


def test_concurrent_requests_share_one_verification(setup, monkeypatch):
    settings, engine, _, _, generation, vectors, embeddings = setup
    run_ingestion(engine, settings, generation, embeddings, vectors)
    entered, release = threading.Event(), threading.Event()
    calls = []
    verify = vectors.verify

    def blocked(*args):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        verify(*args)

    monkeypatch.setattr(vectors, "verify", blocked)
    service = SearchService(engine, settings, vectors, embeddings)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(service.generation, require_vectors=True)
            try:
                assert entered.wait(2)
                second = pool.submit(service.generation, require_vectors=True)
            finally:
                release.set()
            assert first.result().status == second.result().status == "ready"
        assert len(calls) == 1
    finally:
        service.close()


def test_cold_verification_survives_search_timeouts_and_allows_browsing(
    setup, monkeypatch
):
    settings, engine, _, _, generation, vectors, embeddings = setup
    run_ingestion(engine, settings, generation, embeddings, vectors)
    entered, release = threading.Event(), threading.Event()
    calls = []
    verify = vectors.verify

    def blocked(*args):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        verify(*args)

    monkeypatch.setattr(vectors, "verify", blocked)
    monkeypatch.setattr(search_module, "SEARCH_TIMEOUT_SECONDS", 0.1)
    with TestClient(
        create_app(settings, vectors=vectors, embeddings=embeddings)
    ) as client:
        try:
            for _ in range(2):
                response = client.post("/api/v1/search/text", json={"query": "red"})
                assert response.status_code == 504
                assert response.json()["code"] == "search_timeout"
            assert entered.is_set()
            status = client.get("/api/v1/status").json()
            assert status["status"] == "checking"
            assert status["indexed_images"] == 3
            assert not status["search_available"]
            assert len(client.get("/api/v1/images").json()["items"]) == 3
            assert client.get("/api/v1/filters").status_code == 200
            assert len(calls) == 1
        finally:
            release.set()
        # Wait for that same job, without a status request to warm up verification.
        app_search = client.app.state.search
        app_search.generation(require_vectors=True)
        assert (
            client.post("/api/v1/search/text", json={"query": "red"}).status_code == 200
        )
        assert len(calls) == 1


@pytest.mark.parametrize("failure", ["missing_point", "transport"])
def test_failed_check_requires_full_verification_after_recovery(
    setup, monkeypatch, failure
):
    settings, engine, selected, _, generation, vectors, embeddings = setup
    run_ingestion(engine, settings, generation, embeddings, vectors)
    service = SearchService(engine, settings, vectors, embeddings)
    try:
        current = service.generation(require_vectors=True)
        point = vectors.client.retrieve(
            current.collection, [selected[0]["image_id"]], with_vectors=True
        )[0]
        original = models.PointStruct(
            id=point.id, vector=point.vector, payload=point.payload
        )
        check_collection = vectors.check_collection
        if failure == "missing_point":
            vectors.client.delete(
                current.collection, models.PointIdsList(points=[point.id]), wait=True
            )
        else:

            def unavailable(*_args):
                raise ConnectionError("fixture transport failure")

            monkeypatch.setattr(vectors, "check_collection", unavailable)
        assert service.status().status == "unavailable"
        assert not service._verified
        monkeypatch.setattr(vectors, "check_collection", check_collection)
        vectors.client.upsert(
            current.collection,
            [original.model_copy(update={"payload": {"index_version": "wrong"}})],
            wait=True,
        )
        with pytest.raises(SearchError) as error:
            service.search(text="red")
        assert error.value.code == "index_inconsistent"
        assert not service._verified
        vectors.client.upsert(current.collection, [original], wait=True)
        assert service.search(text="red").results[0].title == "red"
    finally:
        service.close()


def test_old_verification_cannot_publish_readiness_for_a_new_generation(
    setup, monkeypatch
):
    settings, engine, selected, report, generation, vectors, embeddings = setup
    run_ingestion(engine, settings, generation, embeddings, vectors)
    entered, release = threading.Event(), threading.Event()
    verify = vectors.verify

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        verify(*args)

    service = SearchService(engine, settings, vectors, embeddings)
    try:
        monkeypatch.setattr(vectors, "verify", blocked)
        assert service.status().status == "checking"
        assert entered.is_set()
        old_job = service._verification_future
        monkeypatch.setattr(vectors, "verify", verify)
        new_generation = create_generation(engine, settings, selected[:1], report)
        run_ingestion(engine, settings, new_generation, embeddings, vectors)
        status = service.status()
        assert status.status == "checking"
        assert status.index_version == new_generation
        release.set()
        with pytest.raises(SearchError):
            old_job.result(timeout=2)
        assert not service._verified
        assert service.generation(require_vectors=True).id == new_generation
    finally:
        release.set()
        service.close()


def test_streamed_verification_rejects_a_short_catalogue(setup):
    settings, engine, _, _, generation, vectors, embeddings = setup
    run_ingestion(engine, settings, generation, embeddings, vectors)
    service = SearchService(engine, settings, vectors, embeddings)
    try:
        current = service.generation()
        with pytest.raises(SearchError, match="counts do not match"):
            vectors.verify(current, iter(service.catalogue.images(generation)[:1]))
    finally:
        service.close()


def test_shutdown_drains_inflight_verification_and_stops_later_batches(
    setup, monkeypatch
):
    settings, engine, selected, report, _, vectors, embeddings = setup
    sample = [dict(selected[0], image_id=str(UUID(int=n + 1))) for n in range(130)]
    generation = create_generation(engine, settings, sample, report)
    run_ingestion(engine, settings, generation, embeddings, vectors)
    entered, release = threading.Event(), threading.Event()
    batches = []
    retrieve = vectors.client.retrieve

    def blocked(collection, ids, **kwargs):
        batches.append(ids)
        entered.set()
        assert release.wait(5)
        return retrieve(collection, ids, **kwargs)

    monkeypatch.setattr(vectors.client, "retrieve", blocked)
    service = SearchService(engine, settings, vectors, embeddings)
    try:
        assert service.status().status == "checking"
        assert entered.is_set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(service.close)
            try:
                assert service._verification_cancel.wait(1)
                assert not closing.done()
            finally:
                release.set()
            closing.result(timeout=2)
        assert len(batches) == 1
    finally:
        release.set()
        service.close()
