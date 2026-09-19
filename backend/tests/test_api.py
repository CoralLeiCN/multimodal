import base64
import json

from app.main import create_app
from app.services.ingestion import run_ingestion
from fastapi.testclient import TestClient


def test_search_browse_similarity_and_image_upload(setup):
    settings, engine, selected, _report, generation_id, vectors, embeddings = setup
    run_ingestion(engine, settings, generation_id, embeddings, vectors)
    with TestClient(
        create_app(settings, vectors=vectors, embeddings=embeddings)
    ) as client:
        assert client.get("/api/v1/status").json()["indexed_images"] == 3
        page = client.get("/api/v1/images?limit=2").json()
        assert len(page["items"]) == 2 and page["next_cursor"]
        next_page = client.get(
            "/api/v1/images", params={"limit": 2, "cursor": page["next_cursor"]}
        ).json()
        assert len(next_page["items"]) == 1 and next_page["next_cursor"] is None
        results = client.post(
            "/api/v1/search/text", json={"query": "red", "limit": 2}
        ).json()
        assert results["results"][0]["title"] == "red"
        red = results["results"][0]
        assert red["score"] > 0.99
        assert red["associations"][0]["credit"] == "Fixture credit"
        assert client.get(red["image_url"]).headers["content-type"] == "image/png"
        before = embeddings.calls
        similar = client.post(
            f"/api/v1/images/{red['image_id']}/similar", json={"limit": 3}
        ).json()
        assert len(similar["results"]) == 2
        assert red["image_id"] not in [item["image_id"] for item in similar["results"]]
        assert embeddings.calls == before
        path = settings.image_root / selected[1]["relative_path"]
        upload = client.post(
            "/api/v1/search/image",
            files={"image": ("green.png", path.read_bytes(), "image/png")},
        )
        assert upload.status_code == 200
        assert upload.json()["results"][0]["title"] == "green"
        assert (
            client.post("/api/v1/search/text", json={"query": "   "}).status_code == 422
        )
        assert (
            client.post(
                "/api/v1/search/image",
                files={"image": ("bad.png", b"not an image", "image/png")},
            ).status_code
            == 422
        )
        assert client.get("/api/v1/images?limit=101").status_code == 422
        cursor = base64.urlsafe_b64encode(
            json.dumps({"version": "other", "last_id": ""}).encode()
        ).decode()
        assert (
            client.get("/api/v1/images", params={"cursor": cursor}).status_code == 409
        )


def test_empty_index_and_qdrant_failure_are_explicit(setup):
    settings, engine, _selected, _report, generation_id, vectors, embeddings = setup
    with TestClient(
        create_app(settings, vectors=vectors, embeddings=embeddings)
    ) as client:
        assert client.get("/api/v1/status").json()["status"] == "empty"
        assert (
            client.post("/api/v1/search/text", json={"query": "red"}).status_code == 503
        )
        run_ingestion(engine, settings, generation_id, embeddings, vectors)
        assert client.get("/api/v1/status").json()["status"] == "ready"
        vectors.client.delete_collection(f"smg_images_{generation_id}")
        assert client.get("/api/v1/status").json()["search_available"] is False
        assert (
            client.post("/api/v1/search/text", json={"query": "red"}).status_code == 503
        )
        assert client.get("/api/v1/images").status_code == 200
