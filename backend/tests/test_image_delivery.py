"""Collection delivery from a read-only HF mount, with no storage redirects."""

import shutil
from uuid import uuid4

import pytest
from app.main import create_app
from app.models import Generation, Image
from app.services.ingestion import run_ingestion
from fastapi.testclient import TestClient
from sqlalchemy import update


def test_api_serves_read_only_mount_and_rechecks_membership(setup, tmp_path):
    settings, engine, selected, _, generation_id, vectors, embeddings = setup
    run_ingestion(engine, settings, generation_id, embeddings, vectors)
    mounted = tmp_path / "hf-images"
    shutil.copytree(settings.image_root, mounted)
    settings.image_root = mounted
    image = selected[0]
    path = mounted / image["relative_path"]
    content = path.read_bytes()
    path.chmod(0o444)
    route = f"/api/v1/images/{image['image_id']}/file"
    with TestClient(
        create_app(settings, vectors=vectors, embeddings=embeddings)
    ) as client:
        response = client.get(route, follow_redirects=False)
        assert response.status_code == 200
        assert response.content == content
        assert response.headers["cache-control"] == "private, max-age=300"
        assert "location" not in response.headers
        assert client.get(f"/api/v1/images/{uuid4()}/file").status_code == 404
        path.unlink()
        missing = client.get(route, follow_redirects=False)
        assert missing.status_code == 404
        assert missing.json()["code"] == "image_missing"
        assert "location" not in missing.headers
        path.write_bytes(content)
        assert client.get(route).content == content
        with engine.begin() as connection:
            connection.execute(
                update(Generation)
                .where(Generation.id == generation_id)
                .values(status="building")
            )
        assert client.get(route).status_code == 503


@pytest.mark.parametrize("escape", ["relative", "absolute", "symlink"])
def test_api_rejects_paths_outside_image_mount(setup, tmp_path, escape):
    settings, engine, selected, _, generation_id, vectors, embeddings = setup
    run_ingestion(engine, settings, generation_id, embeddings, vectors)
    image = selected[0]
    outside = tmp_path / "private.png"
    outside.write_bytes(b"must not be served")
    relative = image["relative_path"]
    if escape == "symlink":
        path = settings.image_root / relative
        path.unlink()
        path.symlink_to(outside)
    else:
        relative = "../private.png" if escape == "relative" else str(outside)
        with engine.begin() as connection:
            connection.execute(
                update(Image)
                .where(Image.image_id == image["image_id"])
                .values(relative_path=relative)
            )
    with TestClient(
        create_app(settings, vectors=vectors, embeddings=embeddings)
    ) as client:
        response = client.get(f"/api/v1/images/{image['image_id']}/file")
        assert response.status_code == 404
        assert outside.read_bytes() not in response.content
