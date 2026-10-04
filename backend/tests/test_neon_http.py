"""HTTPS protocol, real PostgreSQL query parity, and startup without native I/O."""

import json

import httpx
import pytest
from app import main, web
from app.core import db
from app.core.config import Settings
from app.models import Generation
from app.schemas import MetadataFilters
from app.services.ingestion import run_ingestion
from app.services.neon_http import NeonHttpQueries, connection, prepare
from app.services.search import SearchService
from fastapi.testclient import TestClient
from sqlalchemy import delete

NEON_URL = (
    "postgresql://reader:fixture-secret@ep-fixture.eu-west-2.aws.neon.tech/catalogue"
    "?sslmode=verify-full"
)


def https_settings(settings=None):
    values = {"DATABASE_URL": NEON_URL, "catalogue_transport": "neon_http"}
    if settings:
        values = {**settings.model_dump(by_alias=True), **values}
    return Settings(_env_file=None, **values)


def postgres_http(engine, requests):
    """Execute exactly the $n SQL and return Neon-compatible raw PG wire values.

    Only this test stand-in connects to the disposable local PostgreSQL server.
    It validates the compiled SQL, JSONB predicates, bind order and OID decoder.
    A hosted Neon smoke check is still required before deployment.
    """

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://ep-fixture.eu-west-2.aws.neon.tech/sql"
        assert request.headers["Neon-Connection-String"] == NEON_URL
        assert request.headers["Neon-Batch-Read-Only"] == "true"
        assert request.headers["Neon-Batch-Isolation-Level"] == "RepeatableRead"
        assert request.headers["Neon-Raw-Text-Output"] == "true"
        assert request.headers["Neon-Array-Mode"] == "true"
        results = []
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            pg = conn.connection.driver_connection.pgconn
            for item in json.loads(request.content)["queries"]:
                result = pg.exec_params(
                    item["query"].encode(),
                    [
                        value.encode() if value is not None else None
                        for value in item["params"]
                    ],
                )
                assert not result.error_message, result.error_message.decode()
                results.append(
                    {
                        "command": "SELECT",
                        "fields": [
                            {
                                "name": result.fname(i).decode(),
                                "dataTypeID": result.ftype(i),
                            }
                            for i in range(result.nfields)
                        ],
                        "rows": [
                            [
                                None
                                if (v := result.get_value(row, col)) is None
                                else v.decode()
                                for col in range(result.nfields)
                            ]
                            for row in range(result.ntuples)
                        ],
                    }
                )
        return httpx.Response(200, json={"results": results})

    return httpx.MockTransport(handle)


def test_https_matches_native_search_without_native_startup(setup, monkeypatch):
    settings, engine, selected, _, generation, vectors, embeddings = setup
    run_ingestion(engine, settings, generation, embeddings, vectors)
    native = SearchService(engine, settings, vectors, embeddings)
    requests = []
    https = NeonHttpQueries(
        https_settings(settings), transport=postgres_http(engine, requests)
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("HF HTTPS startup must not open PostgreSQL or run migrations")

    monkeypatch.setattr(main, "make_engine", forbidden)
    monkeypatch.setattr(db, "migrate", forbidden)
    monkeypatch.setattr(main, "NeonHttpQueries", lambda _: https)
    app = main.create_app(
        https_settings(settings), vectors=vectors, embeddings=embeddings
    )
    with TestClient(app) as client:
        assert app.state.search.engine is None
        assert client.get("/api/v1/status").json() == native.status().model_dump(
            mode="json"
        )
        for filters in (
            MetadataFilters(),
            MetadataFilters(
                place=["London"], category=["Optics"], date_from=1800, date_to=1900
            ),
            MetadataFilters(place=["Paris", "London"], date_to=1890),
            MetadataFilters(place=["London'); DROP TABLE images; --"]),
        ):
            expected = native.browse(1, filters=filters).model_dump(mode="json")
            actual = app.state.search.browse(1, filters=filters).model_dump(mode="json")
            assert actual == expected
            if actual["next_cursor"]:
                assert app.state.search.browse(
                    1, actual["next_cursor"], filters
                ) == native.browse(1, actual["next_cursor"], filters)
        assert app.state.search.filter_options() == native.filter_options()
        for query in ("red", "green"):
            body = {"query": query, "filters": {"place": ["London"]}}
            response = client.post("/api/v1/search/text", json=body)
            assert response.status_code == 200
            assert response.json()["results"] == [
                item.model_dump(mode="json")
                for item in native.search(
                    text=query, filters=MetadataFilters(place=["London"])
                ).results
            ]
        image_id = selected[0]["image_id"]
        assert client.get(f"/api/v1/images/{image_id}").json() == native.image(
            image_id
        ).model_dump(mode="json")
        path, mime = native.image_source(image_id)
        assert (
            client.get(f"/api/v1/images/{image_id}/file").content == path.read_bytes()
        )
        uploaded = client.post(
            "/api/v1/search/image",
            files={"image": (path.name, path.read_bytes(), mime)},
        )
        assert uploaded.json()["results"] == [
            item.model_dump(mode="json")
            for item in native.search(image=path.read_bytes(), mime_type=mime).results
        ]
        similar = client.post(f"/api/v1/images/{image_id}/similar", json={"limit": 2})
        assert similar.json()["results"] == [
            item.model_dump(mode="json")
            for item in native.search(similar_id=image_id, limit=2).results
        ]
        for record in ("co-red", "co-missing"):
            assert app.state.search.lookup_record(record) == native.lookup_record(
                record
            )
    assert https.client.is_closed
    assert requests
    for request in requests:
        for query in json.loads(request.content)["queries"]:
            assert "DROP TABLE" not in query["query"]


@pytest.mark.parametrize(
    "failure",
    ["timeout", "unauthorized", "redirect", "bad_json", "bad_rows", "short_batch"],
)
def test_http_failures_are_sanitized_and_status_is_unavailable(monkeypatch, failure):
    requests = []
    secret = "fixture-secret SQL private query"

    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout(secret, request=request)
        if failure == "unauthorized":
            return httpx.Response(401, json={"message": secret})
        if failure == "redirect":
            return httpx.Response(
                307, headers={"location": "https://other.example/sql"}
            )
        if failure == "bad_json":
            return httpx.Response(200, text=secret)
        if failure == "short_batch":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={"results": [{"command": "SELECT", "rows": [[secret]], "fields": []}]},
        )

    queries = NeonHttpQueries(https_settings(), transport=httpx.MockTransport(handle))
    monkeypatch.setattr(main, "NeonHttpQueries", lambda _: queries)
    monkeypatch.setattr(main, "make_engine", lambda _: pytest.fail("native connection"))

    class Resource:
        def close(self):
            pass

    with TestClient(
        main.create_app(https_settings(), vectors=Resource(), embeddings=Resource())
    ) as client:
        assert client.get("/api/v1/status").json()["status"] == "unavailable"
        result = client.get("/api/v1/images")
        assert result.status_code == 503
        assert result.json()["code"] == "catalogue_unavailable"
        assert "fixture-secret" not in result.text
    assert len(requests) == 2  # no redirect and no hidden retries


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://reader:secret@localhost/db",
        "postgresql://reader:secret@ep-fake.neon.tech.evil.example/db",
        "postgresql://reader:secret@ep-fake.neon.tech:443/db",
        "postgresql://reader@ep-fake.neon.tech/db",
        NEON_URL + "&host=evil.example",
    ],
)
def test_http_rejects_wrong_endpoints_without_disclosing_credentials(url):
    with pytest.raises(ValueError) as error:
        connection(Settings(_env_file=None, DATABASE_URL=url))
    assert "secret" not in str(error.value)


def test_https_disallows_writes_and_web_sessions():
    with pytest.raises(TypeError, match="SELECT"):
        prepare(delete(Generation))
    with pytest.raises(ValueError, match="conversation transactions"):
        web.create_app(https_settings())


def test_native_startup_does_not_recreate_missing_catalogue_schema(setup):
    settings, engine, _, _, _, vectors, embeddings = setup
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE service_state")
    with TestClient(
        main.create_app(settings, vectors=vectors, embeddings=embeddings)
    ) as client:
        status = client.get("/api/v1/status").json()
        assert status["status"] == "unavailable"
        assert "migrations" in status["message"]
        assert client.get("/api/v1/images").json()["code"] == "catalogue_unavailable"
