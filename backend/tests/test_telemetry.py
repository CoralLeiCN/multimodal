import json
import os
from types import SimpleNamespace

import logfire
import pytest
from app.core import telemetry
from app.core.config import Settings
from app.main import create_app
from app.services.embeddings import GeminiEmbeddings, SearchError
from app.services.ingestion import run_ingestion
from fastapi.testclient import TestClient
from logfire.testing import TestExporter as SpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


@pytest.fixture
def spans(monkeypatch):
    exporter = SpanExporter()
    logfire.configure(
        send_to_logfire=False,
        console=False,
        inspect_arguments=False,
        additional_span_processors=[
            telemetry.RequestPrivacy(),
            SimpleSpanProcessor(exporter),
        ],
        advanced=logfire.AdvancedOptions(exception_callback=telemetry.redact_exception),
    )
    monkeypatch.setattr(telemetry, "_configured", True)
    return exporter


def test_search_spans_are_nested_and_exclude_request_content(setup, spans):
    settings, engine, _selected, _report, run_id, vectors, embeddings = setup
    run_ingestion(engine, settings, run_id, embeddings, vectors)
    secret = "private-search-content-82936"
    with TestClient(
        create_app(settings, vectors=vectors, embeddings=embeddings)
    ) as client:
        response = client.post(
            "/api/v1/search/text",
            params={"unexpected": secret},
            json={"query": secret, "limit": 2},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert response.status_code == 200
        assert (
            client.post(
                "/api/v1/search/text", json={"query": {"invalid": secret}}
            ).status_code
            == 422
        )
    exported = [
        s
        for s in spans.exported_spans
        if s.attributes.get("logfire.span_type") == "span"
    ]
    request = next(s for s in exported if s.name == "POST /api/v1/search/text")
    search = next(s for s in exported if s.name == "search")
    query = next(s for s in exported if s.name == "qdrant.search")
    lookup = next(s for s in exported if s.name == "catalogue.read_images")
    assert search.parent.span_id == request.context.span_id
    assert query.parent.span_id == lookup.parent.span_id == search.context.span_id
    assert secret not in json.dumps(
        spans.exported_spans_as_dict(_include_pending_spans=True)
    )


def test_embedding_span_records_metadata_without_input_or_vector(spans):
    secret = "private-embedding-input-73245"
    settings = Settings(_env_file=None, gemini_api_key=secret, embedding_dimensions=3)
    adapter = GeminiEmbeddings(settings)
    adapter.client = SimpleNamespace(
        models=SimpleNamespace(
            embed_content=lambda **kwargs: SimpleNamespace(
                embeddings=[SimpleNamespace(values=[123.4567, 0.0, 0.0])]
            )
        )
    )
    assert adapter.embed(text=secret) == [1.0, 0.0, 0.0]
    span = next(s for s in spans.exported_spans if s.name == "gemini.embed")
    assert span.attributes["model"] == settings.embedding_model
    assert span.attributes["dimensions"] == 3
    assert span.attributes["input_kind"] == "text"
    output = json.dumps(spans.exported_spans_as_dict())
    assert secret not in output
    assert "123.4567" not in output


def test_provider_failure_records_type_without_exception_content(
    setup, spans, monkeypatch
):
    settings, engine, _selected, _report, run_id, vectors, embeddings = setup
    run_ingestion(engine, settings, run_id, embeddings, vectors)
    secret = "private-provider-payload-61938"

    def fail(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(vectors.client, "query_points", fail)
    from app.services.search import SearchService

    with pytest.raises(SearchError, match="could not complete"):
        SearchService(engine, settings, vectors, embeddings).search(text="red")
    span = next(
        s
        for s in spans.exported_spans
        if s.name == "qdrant.search" and s.attributes.get("logfire.span_type") == "span"
    )
    assert span.attributes["error.type"] == "RuntimeError"
    assert span.status.status_code.name == "ERROR"
    assert secret not in json.dumps(spans.exported_spans_as_dict())


def test_token_and_export_settings_load_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("LOGFIRE_SEND_TO_LOGFIRE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LOGFIRE_TOKEN=test-secret\n"
        "LOGFIRE_INDEXER_TOKEN=indexer-secret\n"
        "LOGFIRE_SEND_TO_LOGFIRE=false\n"
        "LOGFIRE_ENVIRONMENT=staging\n"
    )
    settings = Settings(_env_file=env_file)
    assert settings.logfire_token.get_secret_value() == "test-secret"
    assert settings.logfire_indexer_token.get_secret_value() == "indexer-secret"
    assert settings.logfire_send_to_logfire is False
    assert settings.logfire_environment == "staging"
    assert "test-secret" not in repr(settings)
    assert "indexer-secret" not in repr(settings)


@pytest.mark.parametrize("indexing", [False, True])
@pytest.mark.parametrize("indexer_token", [None, "indexer-token"])
def test_api_and_indexer_use_separate_destinations(
    monkeypatch, indexing, indexer_token
):
    shared = {
        "LOGFIRE_TOKEN": "api-token",
        "LOGFIRE_API_KEY": "api-account-key",
        "LOGFIRE_BASE_URL": "https://api-only.example.com",
    }
    for name, value in shared.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(telemetry, "_configured", False)
    captured = {}

    def configure(**kwargs):
        captured.update(kwargs)
        captured["environment_overrides"] = {
            name: os.environ.get(name) for name in shared
        }

    monkeypatch.setattr(logfire, "configure", configure)
    settings = Settings(_env_file=None, logfire_indexer_token=indexer_token)
    service = "multimodal-indexer" if indexing else "multimodal-api"
    telemetry.configure_telemetry(settings, service, indexing=indexing)
    assert captured["token"] == (indexer_token if indexing else "api-token")
    assert captured["data_dir"] == telemetry.ROOT / (
        ".logfire/indexer" if indexing else ".logfire"
    )
    assert captured["environment_overrides"] == (
        dict.fromkeys(shared) if indexing else shared
    )
    assert {name: os.environ.get(name) for name in shared} == shared


def test_indexer_restores_environment_when_configuration_fails(monkeypatch):
    monkeypatch.setenv("LOGFIRE_TOKEN", "api-token")
    monkeypatch.setattr(telemetry, "_configured", False)

    def fail(**kwargs):
        raise RuntimeError("configuration failed")

    monkeypatch.setattr(logfire, "configure", fail)
    with pytest.raises(RuntimeError, match="configuration failed"):
        telemetry.configure_telemetry(
            Settings(_env_file=None), "multimodal-indexer", indexing=True
        )
    assert os.environ["LOGFIRE_TOKEN"] == "api-token"
    assert not telemetry._configured


def test_concurrent_image_spans_remain_children_of_index_run(setup, spans):
    settings, engine, _, _, run_id, vectors, embeddings = setup
    run_ingestion(engine, settings, run_id, embeddings, vectors)
    exported = [
        span
        for span in spans.exported_spans
        if span.attributes.get("logfire.span_type") == "span"
    ]
    run = next(span for span in exported if span.name == "index.run")
    attempts = [span for span in exported if span.name == "index.image"]
    assert len(attempts) == 3
    assert all(span.parent.span_id == run.context.span_id for span in attempts)
    assert all(span.context.trace_id == run.context.trace_id for span in attempts)
