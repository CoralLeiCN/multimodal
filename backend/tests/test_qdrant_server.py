"""Opt-in verification of actual payload indexes on the local Qdrant server."""

import os
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.models import Generation, Image
from app.schemas import MetadataFilters
from app.services.metadata import PAYLOAD_INDEXES
from app.services.qdrant_store import VectorStore


@pytest.mark.skipif(
    not os.environ.get("TEST_QDRANT_URL"),
    reason="Set TEST_QDRANT_URL to a local test server",
)
def test_server_indexes_and_nested_range_filter():
    settings = Settings(
        _env_file=None, qdrant_url=os.environ["TEST_QDRANT_URL"], embedding_dimensions=3
    )
    vectors = VectorStore(settings)
    generation = Generation(
        id=uuid4().hex,
        collection=f"filter_test_{uuid4().hex}",
        model="fixture",
        dimensions=3,
        config_hash="fixture",
        count=2,
    )
    first = Image(
        generation_id=generation.id,
        image_id=str(uuid4()),
        location="fixture",
        relative_path="fixture",
        checksum="fixture",
        mime_type="image/png",
        width=1,
        height=1,
        title="fixture",
        filter_metadata=[
            {
                "record_uid": "first",
                "place": ["london"],
                "category": ["optics"],
                "date_from": 1850,
                "date_to": 1870,
            },
            {
                "record_uid": "second",
                "place": ["paris"],
                "category": ["history"],
                "date_from": 1900,
                "date_to": 1920,
            },
        ],
    )
    second = first.model_copy(
        update={
            "image_id": str(uuid4()),
            "filter_metadata": [
                {
                    "record_uid": "third",
                    "place": ["london"],
                    "category": ["computing"],
                    "date_from": 1900,
                    "date_to": 1910,
                }
            ],
        }
    )
    try:
        vectors.ensure_collection(generation)
        schema = vectors.client.get_collection(generation.collection).payload_schema
        assert {
            key: value.data_type for key, value in schema.items()
        } == PAYLOAD_INDEXES
        vectors.ensure_collection(generation)  # Repeated setup is safe.
        vectors.upsert(generation, first, [1.0, 0.0, 0.0])
        vectors.upsert(generation, second, [0.0, 1.0, 0.0])
        results = vectors.search(
            generation,
            [1.0, 0.0, 0.0],
            1,
            filters=MetadataFilters(date_from=1900, date_to=1900, place=["London"]),
        )
        assert [str(point.id) for point in results] == [second.image_id]
        assert (
            vectors.search(
                generation,
                [1.0, 0.0, 0.0],
                2,
                exclude=second.image_id,
                filters=MetadataFilters(date_from=1900, date_to=1900, place=["London"]),
            )
            == []
        )
    finally:
        if vectors.client.collection_exists(generation.collection):
            vectors.client.delete_collection(generation.collection)
        vectors.close()
