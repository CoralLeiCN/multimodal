import json

import pytest
from app.main import create_app
from app.models import Association, Generation, Image, Ingestion
from app.schemas import MetadataFilters
from app.services.ingestion import create_generation, run_ingestion
from app.services.metadata import extract_metadata, sqlite_filter
from app.services.metadata_refresh import refresh_metadata
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, select


def test_source_ranges_labels_and_unknown_dates():
    metadata = extract_metadata(
        {
            "creation": {
                "date": [
                    {"from": "1850", "to": "1870", "value": "circa 1860"},
                    {"from": "1850", "to": "1870"},
                    {"from": "-400", "to": "-350"},
                    {"from": "1950-01-01", "to": "1951-12-31"},
                    {"value": "1900"},
                    {"value": "probably eighteenth century"},
                    {"from": "0", "to": "0"},
                    {"from": "2000", "to": "1900"},
                    {"from": "1800", "value": "1800"},
                    {"from": "1900-02-29", "to": "1900-03-01"},
                    {"from": "1950-13-01", "to": "1951-01-01"},
                ],
                "place": [
                    {"summary": {"title": "London"}},
                    {"name": [{"value": "london"}]},
                    {"summary": {"title": "London, England"}},
                ],
            },
            "date": [{"from": "2025", "to": "2025"}],
            "category": [
                {"name": "Optics", "value": "SCM - Optics"},
                {"name": "OPTICS"},
            ],
        }
    )
    assert metadata == {
        "places": ["London", "London, England"],
        "categories": ["Optics"],
        "date_ranges": [
            {"date_from": -400, "date_to": -350},
            {"date_from": 1850, "date_to": 1870},
            {"date_from": 1900, "date_to": 1900},
            {"date_from": 1950, "date_to": 1951},
        ],
    }
    assert extract_metadata({"date": [{"from": "1800", "to": "1810"}]})[
        "date_ranges"
    ] == [{"date_from": 1800, "date_to": 1810}]
    assert extract_metadata({}) == {"places": [], "categories": [], "date_ranges": []}


@pytest.mark.parametrize(
    "date, expected",
    [
        ({"value": "c.1993"}, [(1993, 1993)]),
        ({"value": " C. 1993 "}, [(1993, 1993)]),
        ({"value": "c.-400"}, [(-400, -400)]),
        ({"value": "c.0"}, []),
        ({"value": "c.10000"}, []),
        ({"value": "circa 1993"}, []),
        ({"value": "c.1990-1995"}, []),
        ({"value": "c.1993", "from": "1990", "to": "1995"}, [(1990, 1995)]),
        ({"value": "c.1993", "from": "1990"}, []),
        ({"value": "c.1993", "from": "1995", "to": "1990"}, []),
    ],
)
def test_circa_year_rule_respects_bounds_and_validation(date, expected):
    assert extract_metadata({"creation": {"date": [date]}})["date_ranges"] == [
        {"date_from": start, "date_to": end} for start, end in expected
    ]


@pytest.mark.parametrize(
    "filters",
    [
        {"date_from": 1900, "date_to": 1800},
        {"date_from": 0},
        {"date_to": 10000},
        {"date_from": 1800.5},
        {"place": [" "]},
        {"category": ["a"] * 21},
        {"place": ["x" * 301]},
        {"unknown": "field"},
    ],
)
def test_invalid_filters(filters):
    with pytest.raises(ValidationError):
        MetadataFilters(**filters)


def test_filter_semantics_match_qdrant_and_sqlite_before_top_k(setup):
    settings, engine, selected, report, _generation, vectors, embeddings = setup
    # Red has disjoint dates and a second association. Neither can bridge the gap
    # between London/1850 and Paris/1900 for a London/1900 request.
    red = selected[0]["associations"][0]
    red["date_ranges"].append({"date_from": 2000, "date_to": 2010})
    selected[0]["associations"].append(
        {
            **red,
            "record_uid": "co-other",
            "places": ["Paris"],
            "categories": ["History"],
            "date_ranges": [{"date_from": 1900, "date_to": 1900}],
        }
    )
    generation_id = create_generation(engine, settings, selected, report)
    run_ingestion(engine, settings, generation_id, embeddings, vectors)
    with TestClient(
        create_app(settings, vectors=vectors, embeddings=embeddings)
    ) as client:
        cases = [
            ({"date_from": 1870, "date_to": 1870}, {"red"}),
            ({"date_from": 1871, "date_to": 1899}, set()),
            ({"date_from": 1900, "date_to": 1900, "place": ["London"]}, set()),
            ({"date_to": 1850}, {"red"}),
            ({"date_from": 2011}, set()),
            (
                {"place": [" LONDON ", "Paris"], "category": ["Computing"]},
                {"green", "blue"},
            ),
            ({"place": ["ＰＡＲＩＳ"], "category": ["computing"]}, {"green"}),
            ({"place": ["Paris"], "category": ["Optics"]}, set()),
            ({"category": ["Computing"], "date_to": 1920}, {"green"}),
            ({"place": ["Unknown"]}, set()),
        ]
        for filters, expected in cases:
            search = client.post(
                "/api/v1/search/text", json={"query": "red", "filters": filters}
            )
            assert search.status_code == 200, search.text
            assert {item["title"] for item in search.json()["results"]} == expected
            browse = client.get("/api/v1/images", params=filters)
            assert browse.status_code == 200, browse.text
            assert {item["title"] for item in browse.json()["items"]} == expected
            assert browse.json()["matching_images"] == len(expected)
        result = client.post(
            "/api/v1/search/text",
            json={
                "query": "red",
                "limit": 1,
                "filters": {"category": ["Computing"], "place": ["Paris"]},
            },
        ).json()
        assert [item["title"] for item in result["results"]] == ["green"]
        facets = client.get("/api/v1/filters").json()
        assert facets["places"] == ["London", "Paris"]
        assert facets["categories"] == ["Computing", "History", "Optics"]
        assert (facets["date_min"], facets["date_max"]) == (1850, 2010)
        page = client.get(
            "/api/v1/images", params={"limit": 1, "place": ["London"]}
        ).json()
        assert page["next_cursor"]
        assert (
            client.get(
                "/api/v1/images",
                params={"cursor": page["next_cursor"], "place": ["Paris"]},
            ).status_code
            == 409
        )
        next_page = client.get(
            "/api/v1/images",
            params={"cursor": page["next_cursor"], "place": [" london "]},
        ).json()
        assert len(next_page["items"]) == 1


def test_image_similarity_and_invalid_filter_requests(setup):
    settings, engine, selected, _report, generation, vectors, embeddings = setup
    run_ingestion(engine, settings, generation, embeddings, vectors)
    with TestClient(
        create_app(settings, vectors=vectors, embeddings=embeddings)
    ) as client:
        image = settings.image_root / selected[0]["relative_path"]
        result = client.post(
            "/api/v1/search/image",
            files={"image": ("red.png", image.read_bytes(), "image/png")},
            data={"date_from": "1900", "place": ["Paris", "London"]},
        )
        assert result.status_code == 200, result.text
        assert [item["title"] for item in result.json()["results"]] == ["green"]
        similar = client.post(
            f"/api/v1/images/{selected[0]['image_id']}/similar",
            json={"filters": {"date_to": 1870}},
        )
        assert similar.json()["results"] == []
        before = embeddings.calls
        assert (
            client.get("/api/v1/images?date_from=1900&date_to=1800").status_code == 422
        )
        assert (
            client.post(
                "/api/v1/search/text",
                json={"query": "red", "filters": {"date_from": 1900, "date_to": 1800}},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/search/image",
                files={"image": ("red.png", image.read_bytes(), "image/png")},
                data={"date_from": "1900", "date_to": "1800"},
            ).status_code
            == 422
        )
        assert embeddings.calls == before
        # Browse/filter options remain available when Qdrant is down.
        vectors.client.delete_collection(f"smg_images_{generation}")
        assert client.get("/api/v1/images?place=Paris").json()["matching_images"] == 1
        assert client.get("/api/v1/filters").status_code == 200


@pytest.mark.parametrize("ready", [False, True])
def test_refresh_preserves_vectors_and_ingestion_state(setup, tmp_path, ready):
    settings, engine, selected, _report, generation_id, vectors, embeddings = setup
    if ready:
        run_ingestion(engine, settings, generation_id, embeddings, vectors)
    metadata = tmp_path / "fixture.json"
    metadata.write_text(
        json.dumps(
            [
                {
                    "@admin": {"uid": item["associations"][0]["record_uid"]},
                    "creation": {
                        "date": [{"value": "c.1993"}],
                        "place": [{"summary": {"title": "Edinburgh"}}],
                    },
                    "category": [{"name": "Science"}],
                }
                for item in selected
            ]
        )
    )
    calls = embeddings.calls
    with pytest.raises(ValueError, match="nothing was changed"):
        refresh_metadata(engine, settings, generation_id, [metadata], 2, vectors)
    with Session(engine) as session:
        assert session.get(
            Image, (generation_id, selected[0]["image_id"])
        ).filter_metadata[0]["place"] == ["london"]
    result = refresh_metadata(engine, settings, generation_id, [metadata], 3, vectors)
    assert result["payloads_updated"] == (3 if ready else 0)
    assert embeddings.calls == calls
    with Session(engine) as session:
        generation = session.get(Generation, generation_id)
        images = session.exec(
            select(Image).where(Image.generation_id == generation_id)
        ).all()
        assert all(
            image.filter_metadata[0]["place"] == ["edinburgh"] for image in images
        )
        associations = session.exec(
            select(Association).where(Association.generation_id == generation_id)
        ).all()
        assert all(association.date == "c.1993" for association in associations)
        assert all(
            association.date_ranges == [{"date_from": 1993, "date_to": 1993}]
            for association in associations
        )
        ingestions = session.exec(select(Ingestion)).all()
        assert {item.status for item in ingestions} == (
            {"indexed"} if ready else {"pending"}
        )
    if ready:
        vectors.verify(generation, images)
        points = vectors.search(
            generation, [1.0, 0.0, 0.0], 1, filters=MetadataFilters(place=["Edinburgh"])
        )
        assert str(points[0].id) == selected[0]["image_id"]
        for year, expected in [(1992, 0), (1993, 3), (1994, 0)]:
            filters = MetadataFilters(date_from=year, date_to=year)
            assert len(
                vectors.search(generation, [1.0, 0.0, 0.0], 10, filters=filters)
            ) == expected
            with Session(engine) as session:
                assert len(
                    session.exec(
                        select(Image).where(
                            Image.generation_id == generation_id,
                            sqlite_filter(filters),
                        )
                    ).all()
                ) == expected
    assert (
        refresh_metadata(engine, settings, generation_id, [metadata], 3, vectors)
        == result
    )
