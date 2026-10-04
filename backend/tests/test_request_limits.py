import asyncio
import json
from types import SimpleNamespace

import pytest
from app.core.config import Settings
from app.core.request_limits import RequestBodyLimitMiddleware
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.mark.parametrize("declared", [None, b"1"])
@pytest.mark.parametrize("path", ["/api/v1/search/image", "/api/v1/agent/assets"])
def test_oversized_chunks_stop_before_parsing(monkeypatch, declared, path):
    monkeypatch.setattr("app.core.request_limits.MULTIPART_OVERHEAD", 0)
    reads, dispatched, messages = [], [], []
    chunks = iter([b"123456", b"789012", b"never-read"])

    async def receive():
        chunk = next(chunks)
        reads.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def endpoint(*args):
        dispatched.append(True)

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-length", declared)] if declared else [],
        "app": SimpleNamespace(state=SimpleNamespace(agent=None)),
    }
    asyncio.run(RequestBodyLimitMiddleware(endpoint, 10, upload_paths=(path,))(scope, receive, send))
    assert reads == [b"123456", b"789012"]
    assert not dispatched
    assert messages[0]["status"] == 413
    assert json.loads(messages[1]["body"])["code"] == "body_too_large"


def test_body_replay_preserves_bytes_and_disconnect():
    received = []
    messages = iter(
        [
            {"type": "http.request", "body": b"first", "more_body": True},
            {"type": "http.request", "body": b"second", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )

    async def receive():
        return next(messages)

    async def endpoint(scope, receive, send):
        received.append(await receive())
        received.append(await receive())

    async def send(message):
        pytest.fail("The middleware should not send an error")

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/search/text",
        "headers": [],
    }
    asyncio.run(RequestBodyLimitMiddleware(endpoint)(scope, receive, send))
    assert received == [
        {"type": "http.request", "body": b"firstsecond", "more_body": False},
        {"type": "http.disconnect"},
    ]


@pytest.mark.parametrize(
    "path", ["/api/v1/search/text", "/api/v1/search/image", "/api/v1/search/image/"]
)
def test_search_rejects_declared_oversized_bodies_without_services(path):
    # No lifespan: a size rejection must not reach catalogue or embedding services.
    app = create_app(Settings(_env_file=None, max_image_bytes=10))
    client = TestClient(app)
    try:
        response = client.post(
            path, content=b"small", headers={"Content-Length": str(12 * 1024 * 1024)}
        )
    finally:
        client.close()
    assert response.status_code == 413
    assert response.json()["code"] == "body_too_large"
