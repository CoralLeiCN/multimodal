from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from app.core.config import Settings
from app.main import create_app
from app.models import Generation, Image
from app.services.embeddings import SearchError
from app.services.image_delivery import ImageDelivery
from app.services.ingestion import run_ingestion
from fastapi.testclient import TestClient
from sqlalchemy import update

ENDPOINT = "https://" + "a" * 32 + ".r2.cloudflarestorage.com"


def cloud_image(path="image.jpg", **overrides):
    return SimpleNamespace(
        **{
            "relative_path": path,
            "r2_url": f"{ENDPOINT}/smg-images/{quote(path, safe='/')}",
            "generation_id": "generation-one",
            "checksum": "checksum-one",
            **overrides,
        }
    )


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
    image = cloud_image(path)
    delivery = ImageDelivery(settings)
    try:
        signed = urlsplit(delivery.signed_url(image))
        assert signed.netloc == urlsplit(ENDPOINT).netloc
        assert signed.path == "/smg-images/folder/a%20b%23%25.jpg"
        query = parse_qs(signed.query)
        assert query["X-Amz-Expires"] == ["300"]
        assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
        assert query["response-cache-control"] == ["private, max-age=300"]
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
    image = cloud_image()
    with pytest.raises(SearchError) as exc:
        ImageDelivery(settings).signed_url(image)
    assert exc.value.status == 503


def test_api_prefers_local_then_r2_and_checks_membership(setup, monkeypatch):
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
    local_path = settings.image_root / image["relative_path"]
    local_bytes = local_path.read_bytes()

    class Signer:
        calls = 0
        closed = False

        def generate_presigned_url(self, operation, *, Params, ExpiresIn, HttpMethod):
            self.calls += 1
            assert operation == "get_object" and HttpMethod == "GET"
            assert Params == {
                "Bucket": "smg-images",
                "Key": image["relative_path"],
                "ResponseCacheControl": "private, max-age=300",
            }
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
        local = client.get(
            f"/api/v1/images/{image['image_id']}/file", follow_redirects=False
        )
        assert local.status_code == 200
        assert local.content == local_bytes
        assert local.headers["cache-control"] == "private, max-age=300"
        assert signer.calls == 0
        local_path.unlink()
        result = client.get(
            f"/api/v1/images/{image['image_id']}/file", follow_redirects=False
        )
        assert result.status_code == 307
        assert result.headers["cache-control"] == "no-store"
        assert result.headers["location"].endswith("?signature=fake")
        repeated = client.get(
            f"/api/v1/images/{image['image_id']}/file", follow_redirects=False
        )
        assert repeated.headers["location"] == result.headers["location"]
        # A file restored locally takes precedence over an already cached URL.
        local_path.write_bytes(local_bytes)
        restored = client.get(
            f"/api/v1/images/{image['image_id']}/file", follow_redirects=False
        )
        assert restored.status_code == 200
        assert restored.content == local_bytes
        assert signer.calls == 1
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
        (settings.image_root / selected[1]["relative_path"]).unlink()
        unavailable = client.get(
            f"/api/v1/images/{selected[1]['image_id']}/file", follow_redirects=False
        )
        assert unavailable.status_code == 404
        assert signer.calls == 1
        # A cached signature must not bypass an inactive catalogue.
        with engine.begin() as connection:
            connection.execute(
                update(Generation)
                .where(Generation.id == generation_id)
                .values(status="building")
            )
        inactive = client.get(
            f"/api/v1/images/{image['image_id']}/file", follow_redirects=False
        )
        assert inactive.status_code == 503
        assert "location" not in inactive.headers
        assert signer.calls == 1
    assert signer.closed


@pytest.fixture
def cached_delivery(monkeypatch):
    from unittest.mock import Mock

    signer = Mock()
    signer.generate_presigned_url.side_effect = (
        f"https://example.test/image?signature={i}" for i in range(100)
    )
    clock = [0.0]
    monkeypatch.setattr("app.services.image_delivery.monotonic", lambda: clock[0])
    monkeypatch.setattr("app.services.image_delivery.r2_client", lambda _: signer)
    delivery = ImageDelivery(configure(Settings(_env_file=None)))
    yield delivery, signer, clock
    delivery.close()


def test_url_cache_refreshes_before_expiry_without_sliding_ttl(cached_delivery):
    delivery, signer, clock = cached_delivery
    image = cloud_image()
    first = delivery.signed_url(image)
    clock[0] = 239.9
    assert delivery.signed_url(image) == first
    clock[0] = 240
    assert delivery.signed_url(image) != first
    assert signer.generate_presigned_url.call_count == 2


def test_url_cache_is_bounded_and_evicts_least_recently_used(
    cached_delivery, monkeypatch
):
    delivery, signer, _ = cached_delivery
    monkeypatch.setattr("app.services.image_delivery.SIGNED_URL_CACHE_SIZE", 2)
    first = delivery.signed_url(cloud_image("first.jpg"))
    second = delivery.signed_url(cloud_image("second.jpg"))
    assert delivery.signed_url(cloud_image("first.jpg")) == first
    delivery.signed_url(cloud_image("third.jpg"))
    assert delivery.signed_url(cloud_image("second.jpg")) != second
    assert signer.generate_presigned_url.call_count == 4
    assert len(delivery._urls) == 2


@pytest.mark.parametrize("field", ["generation_id", "checksum"])
def test_url_cache_separates_catalogue_versions(cached_delivery, field):
    delivery, signer, _ = cached_delivery
    first = delivery.signed_url(cloud_image())
    assert delivery.signed_url(cloud_image(**{field: "changed"})) != first
    assert signer.generate_presigned_url.call_count == 2


def test_url_cache_still_rejects_changed_storage_reference(cached_delivery):
    delivery, signer, _ = cached_delivery
    image = cloud_image()
    delivery.signed_url(image)
    image.r2_url = "https://untrusted.example/image.jpg"
    with pytest.raises(SearchError):
        delivery.signed_url(image)
    assert signer.generate_presigned_url.call_count == 1


def test_concurrent_requests_share_one_signature(cached_delivery):
    delivery, signer, _ = cached_delivery
    with ThreadPoolExecutor(max_workers=8) as pool:
        urls = list(pool.map(lambda _: delivery.signed_url(cloud_image()), range(32)))
    assert len(set(urls)) == 1
    assert signer.generate_presigned_url.call_count == 1


def test_failed_signing_is_not_cached_and_close_clears_cache(cached_delivery):
    from botocore.exceptions import BotoCoreError

    delivery, signer, _ = cached_delivery
    signer.generate_presigned_url.side_effect = [BotoCoreError(), "first", "second"]
    with pytest.raises(SearchError):
        delivery.signed_url(cloud_image())
    assert delivery.signed_url(cloud_image()) == "first"
    delivery.close()
    signer.close.assert_called_once()
    assert delivery.signed_url(cloud_image()) == "second"
