"""Both application editions and collection tools consume one published index."""

import os

import pytest
from app import main, web
from app.explore.config import ExplorerSettings
from app.explore.tools import call
from app.services.ingestion import run_ingestion
from app.services.qdrant_store import VectorStore
from fastapi.testclient import TestClient


def make_app(edition, settings, tmp_path, **kwargs):
    if edition == "web":
        return web.create_app(
            settings,
            explorer_settings=ExplorerSettings(
                _env_file=None, state_dir=tmp_path / "private-web-state"
            ),
            **kwargs,
        )
    return main.create_app(settings, **kwargs)


@pytest.mark.parametrize("edition", ["search", "web"])
@pytest.mark.parametrize(
    "changed",
    [{"qdrant_collection_name": "wrong_collection"}, {"embedding_revision": "a" * 40}],
)
def test_both_editions_refuse_mismatched_retrieval_settings(
    setup, tmp_path, edition, changed
):
    settings, engine, _, _, generation, vectors, embeddings = setup
    run_ingestion(engine, settings, generation, embeddings, vectors)
    app = make_app(
        edition,
        settings.model_copy(update=changed),
        tmp_path,
        vectors=vectors,
        embeddings=embeddings,
    )
    with TestClient(app) as client:
        before = embeddings.calls
        status = client.get("/api/v1/status").json()
        assert status["status"] == "unavailable"
        response = client.post("/api/v1/search/text", json={"query": "red"})
        assert response.status_code == 503
        assert response.json()["code"] == "index_mismatch"
        assert embeddings.calls == before
        # Read-only metadata browsing can still explain the indexed collection.
        assert client.get("/api/v1/images").status_code == 200


@pytest.mark.skipif(
    not os.environ.get("TEST_QDRANT_URL"),
    reason="Set TEST_QDRANT_URL to a disposable Qdrant server",
)
def test_editions_and_codex_tools_share_one_published_qdrant_collection(
    setup, tmp_path, monkeypatch
):
    settings, engine, selected, _, generation, _, embeddings = setup
    collection = f"smg_images_{generation}"
    settings = settings.model_copy(
        update={
            "qdrant_url": os.environ["TEST_QDRANT_URL"],
            "qdrant_api_key": None,
            "qdrant_collection_name": collection,
        }
    )
    writer = VectorStore(settings)
    try:
        # Publish once, then start independent API instances and Qdrant clients.
        run_ingestion(engine, settings, generation, embeddings, writer)
        original_points = writer.client.scroll(
            collection, limit=100, with_vectors=True
        )[0]
        original_collections = {
            c.name for c in writer.client.get_collections().collections
        }

        def forbidden_write(*_args, **_kwargs):
            pytest.fail(
                "An application attempted to publish or write collection vectors"
            )

        monkeypatch.setattr(VectorStore, "ensure_collection", forbidden_write)
        monkeypatch.setattr(VectorStore, "upsert", forbidden_write)
        search_app = make_app(
            "search", settings, tmp_path, embeddings=type(embeddings)()
        )
        web_app = make_app("web", settings, tmp_path, embeddings=type(embeddings)())
        with TestClient(search_app) as search, TestClient(web_app) as companion:
            for client in (search, companion):
                status = client.get("/api/v1/status").json()
                assert status["status"] == "ready"
                assert status["index_version"] == generation
                assert status["indexed_images"] == len(selected)
            body = {"query": "red", "limit": 3, "filters": {"place": ["London"]}}
            plain = search.post("/api/v1/search/text", json=body).json()
            conversational = companion.post("/api/v1/search/text", json=body).json()
            agent = call(
                web_app.state.explorer.search, "search_text", body, lambda _: None
            )["result"]
            assert plain["results"] == conversational["results"] == agent["results"]
            assert (
                plain["index_version"]
                == conversational["index_version"]
                == agent["index_version"]
                == generation
            )
            image = plain["results"][0]
            assert image["associations"][0]["credit"] == "Fixture credit"
            assert (
                search.get(image["image_url"]).content
                == companion.get(image["image_url"]).content
            )
            similar_body = {"image_id": image["image_id"], "limit": 3}
            agent_similar = call(
                web_app.state.explorer.search,
                "find_similar",
                similar_body,
                lambda _: None,
            )["result"]
            similar = search.post(
                f"/api/v1/images/{image['image_id']}/similar", json={"limit": 3}
            ).json()
            assert similar["results"] == agent_similar["results"]
            assert image["image_id"] not in [
                r["image_id"] for r in agent_similar["results"]
            ]
            assert search.get("/api/v1/explorer/status").status_code == 404
            assert companion.get("/api/v1/explorer/status").status_code == 200
        assert (
            writer.client.scroll(collection, limit=100, with_vectors=True)[0]
            == original_points
        )
        assert {
            c.name for c in writer.client.get_collections().collections
        } == original_collections
    finally:
        if writer.client.collection_exists(collection):
            writer.client.delete_collection(collection)
        writer.close()
