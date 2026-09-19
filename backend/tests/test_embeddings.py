from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from app.core.config import Settings
from app.services.embeddings import GeminiEmbeddings


def test_concurrent_requests_share_client_without_serializing_calls(monkeypatch):
    start = Barrier(10, timeout=10)
    requests = Barrier(10, timeout=10)
    clients = []
    closed = []

    def embed_content(**kwargs):
        requests.wait()
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[1, 0, 0])])

    def create_client(**kwargs):
        client = SimpleNamespace(
            models=SimpleNamespace(embed_content=embed_content),
            close=lambda: closed.append(True),
        )
        clients.append(client)
        return client

    monkeypatch.setattr("app.services.embeddings.genai.Client", create_client)
    adapter = GeminiEmbeddings(
        Settings(
            _env_file=None,
            gemini_api_key="test-key",
            embedding_dimensions=3,
        )
    )

    def embed(_):
        start.wait()
        return adapter.embed(image=b"test", mime_type="image/png")

    try:
        with ThreadPoolExecutor(max_workers=10) as executor:
            assert list(executor.map(embed, range(10))) == [[1, 0, 0]] * 10
        assert len(clients) == 1
    finally:
        adapter.close()
    assert closed == [True]


def test_sdk_batches_images_in_one_http_request_with_separate_content():
    import base64
    import json

    import httpx
    from google import genai
    from google.genai import types

    requests = []

    def handle(request):
        assert request.url.path.endswith("gemini-embedding-2:batchEmbedContents")
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "embeddings": [
                    {"values": [2, 0, 0]},
                    {"values": [0, 3, 0]},
                ]
            },
        )

    adapter = GeminiEmbeddings(
        Settings(
            _env_file=None,
            gemini_api_key="test-key",
            embedding_dimensions=3,
        )
    )
    adapter.client = genai.Client(
        api_key="test-key",
        http_options=types.HttpOptions(
            client_args={"transport": httpx.MockTransport(handle)}
        ),
    )
    try:
        assert adapter.embed_images(
            [(b"first", "image/png"), (b"second", "image/jpeg")]
        ) == [
            [1, 0, 0],
            [0, 1, 0],
        ]
    finally:
        adapter.close()
    assert len(requests) == 1
    entries = requests[0]["requests"]
    assert len(entries) == 2
    for entry, (data, mime) in zip(
        entries, [(b"first", "image/png"), (b"second", "image/jpeg")], strict=True
    ):
        assert entry["outputDimensionality"] == 3
        assert entry["content"]["parts"] == [
            {
                "inline_data": {
                    "data": base64.b64encode(data).decode(),
                    "mime_type": mime,
                }
            }
        ]


def test_batch_adapter_rejects_aggregated_or_invalid_vectors():
    import pytest
    from app.services.embeddings import SearchError

    adapter = GeminiEmbeddings(
        Settings(
            _env_file=None,
            gemini_api_key="test-key",
            embedding_dimensions=3,
        )
    )
    for values in ([[1, 0, 0]], [[1, 0, 0], [0, 0]], [[1, 0, 0], [float("nan"), 0, 0]]):
        adapter.client = SimpleNamespace(
            models=SimpleNamespace(
                embed_content=lambda values=values, **kwargs: SimpleNamespace(
                    embeddings=[SimpleNamespace(values=value) for value in values],
                ),
            )
        )
        with pytest.raises(SearchError) as error:
            adapter.embed_images([(b"first", "image/png"), (b"second", "image/png")])
        assert error.value.code == "invalid_embedding"
