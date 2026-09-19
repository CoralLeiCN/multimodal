from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from app.core.config import Settings
from app.main import create_app
from app.models import Image
from app.services.embeddings import SearchError
from app.services.image_delivery import ImageDelivery
from app.services.ingestion import run_ingestion
from fastapi.testclient import TestClient
from sqlalchemy import update

ENDPOINT = "https://" + "a" * 32 + ".r2.cloudflarestorage.com"


def configure(settings):
    return settings.model_copy(
        update={
            "r2_endpoint_url": ENDPOINT,
            "r2_bucket": "smg-images",
        }
    )


def test_presigned_url_uses_exact_verified_key_and_expires():
    settings = Settings(
        _env_file=None,
        r2_endpoint_url=ENDPOINT,
        r2_bucket="smg-images",
        r2_access_key_id="test-access-key",
        r2_secret_access_key="test-secret-key",
    )
    path = "folder/a b#%.jpg"
    image = SimpleNamespace(
        relative_path=path, r2_url=f"{ENDPOINT}/smg-images/{quote(path, safe='/')}"
    )
    delivery = ImageDelivery(settings)
    try:
        signed = urlsplit(delivery.signed_url(image))
        assert signed.netloc == urlsplit(ENDPOINT).netloc
        assert signed.path == "/smg-images/folder/a%20b%23%25.jpg"
        query = parse_qs(signed.query)
        assert query["X-Amz-Expires"] == ["300"]
        assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
        assert "test-secret-key" not in signed.geturl()
    finally:
        delivery.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://untrusted.example/image.jpg",
        f"{ENDPOINT}/other-bucket/image.jpg",
        f"{ENDPOINT}/smg-images/other.jpg",
        f"{ENDPOINT}/smg-images/image.jpg?X-Amz-Signature=stale",
    ],
)
def test_rejects_unverified_destinations_before_signing(url, monkeypatch):
    def forbidden(_settings):
        raise AssertionError("Must validate stored URL before creating a signer")

    monkeypatch.setattr("app.services.image_delivery.r2_client", forbidden)
    settings = configure(Settings(_env_file=None))
    image = SimpleNamespace(relative_path="image.jpg", r2_url=url)
    with pytest.raises(SearchError) as exc:
        ImageDelivery(settings).signed_url(image)
    assert exc.value.status == 503
    assert exc.value.code == "image_storage_unavailable"
    assert url not in str(exc.value)


def test_local_mode_and_unlinked_images_need_no_cloud_credentials():
    image = SimpleNamespace(relative_path="image.jpg", r2_url="some-url")
    assert ImageDelivery(Settings(_env_file=None)).signed_url(image) is None
    image.r2_url = None
    assert ImageDelivery(configure(Settings(_env_file=None))).signed_url(image) is None


def test_missing_credentials_are_a_sanitized_service_error():
    settings = configure(Settings(_env_file=None))
    image = SimpleNamespace(
        relative_path="image.jpg", r2_url=f"{ENDPOINT}/smg-images/image.jpg"
    )
    with pytest.raises(SearchError) as exc:
        ImageDelivery(settings).signed_url(image)
    assert exc.value.status == 503


def test_api_redirects_without_local_file_and_checks_membership(setup, monkeypatch):
    settings, engine, selected, _report, generation_id, vectors, embeddings = setup
    run_ingestion(engine, settings, generation_id, embeddings, vectors)
    settings = configure(settings)
    image = selected[0]
    with engine.begin() as connection:
        connection.execute(
            update(Image)
            .where(Image.image_id == image["image_id"])
            .values(r2_url=f"{ENDPOINT}/smg-images/{image['relative_path']}")
        )
    (settings.image_root / image["relative_path"]).unlink()

    class Signer:
        calls = 0
        closed = False

        def generate_presigned_url(self, operation, *, Params, ExpiresIn, HttpMethod):
            self.calls += 1
            assert operation == "get_object" and HttpMethod == "GET"
            assert Params == {"Bucket": "smg-images", "Key": image["relative_path"]}
            assert ExpiresIn == 300
            return f"{ENDPOINT}/smg-images/{image['relative_path']}?signature=fake"

        def close(self):
            self.closed = True

    signer = Signer()
    monkeypatch.setattr(
        "app.services.image_delivery.r2_client", lambda _settings: signer
    )
    with TestClient(
        create_app(settings, vectors=vectors, embeddings=embeddings)
    ) as client:
        result = client.get(
            f"/api/v1/images/{image['image_id']}/file", follow_redirects=False
        )
        assert result.status_code == 307
        assert result.headers["cache-control"] == "no-store"
        assert result.headers["location"].endswith("?signature=fake")
        missing = client.get(
            "/api/v1/images/00000000-0000-0000-0000-000000000000/file",
            follow_redirects=False,
        )
        assert missing.status_code == 404
        assert signer.calls == 1
        # Unlinked catalogue images continue to use the validated local file.
        assert (
            client.get(f"/api/v1/images/{selected[1]['image_id']}/file").status_code
            == 200
        )
    assert signer.closed
