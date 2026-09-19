import logfire
from qdrant_client import QdrantClient, models

from app.core.config import Settings
from app.models import Generation, Image
from app.schemas import MetadataFilters
from app.services.embeddings import SearchError, normalize
from app.services.metadata import PAYLOAD_INDEXES, PAYLOAD_SCHEMA_VERSION, qdrant_filter


class VectorStore:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.client = client or QdrantClient(
            url=settings.qdrant_url,
            timeout=10,
            api_key=settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key
            else None,
        )

    @logfire.instrument("qdrant.ensure_collection", extract_args=False)
    def ensure_collection(self, generation: Generation):
        if not self.client.collection_exists(generation.collection):
            self.client.create_collection(
                generation.collection,
                vectors_config=models.VectorParams(
                    size=generation.dimensions, distance=models.Distance.COSINE
                ),
            )
        if self.settings.qdrant_collection_name == generation.collection:
            offset = None
            while True:
                points, offset = self.client.scroll(
                    generation.collection,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                if any(
                    (point.payload or {}).get("index_version") != generation.id
                    for point in points
                ):
                    raise SearchError(
                        "The permanent collection belongs to another catalogue. "
                        "Use its matching catalogue database or an empty collection.",
                        "index_mismatch",
                    )
                if offset is None:
                    break
        self.check_collection(generation)
        existing = self.client.get_collection(generation.collection).payload_schema
        for field, schema in PAYLOAD_INDEXES.items():
            if field in existing and existing[field].data_type != schema:
                raise SearchError(
                    "A metadata index has an incompatible type.", "index_mismatch"
                )
            if field not in existing:
                self.client.create_payload_index(
                    generation.collection,
                    field_name=field,
                    field_schema=schema,
                    wait=True,
                )

    def check_collection(self, generation: Generation):
        info = self.client.get_collection(generation.collection)
        vectors = info.config.params.vectors
        if (
            isinstance(vectors, dict)
            or vectors.size != generation.dimensions
            or vectors.distance != models.Distance.COSINE
        ):
            raise SearchError(
                "The vector collection configuration does not match this index.",
                "index_mismatch",
            )

    def expected_payload(self, generation: Generation, image: Image):
        return {
            "image_id": image.image_id,
            "image_sha256": image.checksum,
            "embedding_config_hash": generation.config_hash,
            "index_version": generation.id,
            "metadata_schema_version": PAYLOAD_SCHEMA_VERSION,
            "metadata": image.filter_metadata,
        }

    def matches(self, generation: Generation, image: Image) -> bool:
        points = self.client.retrieve(
            generation.collection, [image.image_id], with_vectors=True
        )
        if not points or points[0].payload != self.expected_payload(generation, image):
            return False
        try:
            normalize(points[0].vector, generation.dimensions)
        except (SearchError, TypeError):
            return False
        return True

    @logfire.instrument("qdrant.upsert", extract_args=False)
    def upsert(self, generation: Generation, image: Image, vector: list[float]):
        result = self.client.upsert(
            generation.collection,
            [
                models.PointStruct(
                    id=image.image_id,
                    vector=vector,
                    payload=self.expected_payload(generation, image),
                )
            ],
            wait=True,
        )
        if result.status != models.UpdateStatus.COMPLETED or not self.matches(
            generation, image
        ):
            raise SearchError(
                "Qdrant did not confirm the image write.", "vector_write_failed"
            )

    @logfire.instrument("qdrant.search", extract_args=False)
    def search(
        self,
        generation: Generation,
        vector: list[float],
        limit: int,
        exclude: str | None = None,
        filters: MetadataFilters | None = None,
    ):
        query_filter = qdrant_filter(filters or MetadataFilters(), exclude)
        response = self.client.query_points(
            generation.collection,
            query=vector,
            limit=limit,
            query_filter=query_filter,
            search_params=models.SearchParams(exact=generation.count <= 1000),
            with_payload=True,
            timeout=10,
        )
        return sorted(response.points, key=lambda point: (-point.score, str(point.id)))

    @logfire.instrument("qdrant.vector", extract_args=False)
    def vector(self, generation: Generation, image_id: str) -> list[float]:
        points = self.client.retrieve(
            generation.collection, [image_id], with_vectors=True
        )
        if not points:
            raise SearchError(
                "This image is missing from the vector index.", "index_inconsistent"
            )
        return normalize(points[0].vector, generation.dimensions)

    @logfire.instrument("qdrant.verify", extract_args=False)
    def verify(self, generation: Generation, images: list[Image]) -> None:
        self.check_collection(generation)
        if self.client.count(generation.collection, exact=True).count != len(images):
            raise SearchError(
                "The catalogue and vector counts do not match.", "index_inconsistent"
            )
        for offset in range(0, len(images), 64):
            batch = images[offset : offset + 64]
            points = {
                str(point.id): point
                for point in self.client.retrieve(
                    generation.collection,
                    [image.image_id for image in batch],
                    with_vectors=True,
                )
            }
            for image in batch:
                point = points.get(image.image_id)
                if not point or point.payload != self.expected_payload(
                    generation, image
                ):
                    raise SearchError(
                        "The catalogue and vector contents do not match.",
                        "index_inconsistent",
                    )
                normalize(point.vector, generation.dimensions)

    def close(self):
        self.client.close()
