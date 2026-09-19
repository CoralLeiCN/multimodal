import base64
import hashlib
import json
import threading
from contextlib import contextmanager
from time import perf_counter

import logfire
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import Settings
from app.models import Association, Generation, Image, ServiceState
from app.schemas import (
    AssociationRead,
    BrowseResponse,
    FilterOptions,
    ImageRead,
    MetadataFilters,
    SearchResponse,
    StatusResponse,
)
from app.services.embeddings import SearchError
from app.services.metadata import catalogue_filter, labels
from app.services.selection import safe_path


class SearchService:
    def __init__(self, engine, settings: Settings, vectors, embeddings):
        self.engine, self.settings = engine, settings
        self.vectors, self.embeddings = vectors, embeddings
        self._active = None
        self._verified = False
        self._slots = threading.BoundedSemaphore(4)

    def generation(self, require_vectors=False) -> Generation:
        with Session(self.engine) as session:
            state = session.get(ServiceState, 1)
            generation = (
                session.get(Generation, state.active_generation) if state else None
            )
        if not generation or generation.status != "ready":
            self._active, self._verified = None, False
            raise SearchError(
                "The collection is being prepared. No image index is ready yet.",
                "index_empty",
            )
        if self._active is None or (self._active.id, self._active.completed_at) != (
            generation.id,
            generation.completed_at,
        ):
            self._verified = False
        self._active = generation
        if require_vectors:
            if generation.config_hash != self.settings.config_hash:
                raise SearchError(
                    "Backend embedding settings do not match the active collection.",
                    "index_mismatch",
                )
            try:
                if not self._verified:
                    with Session(self.engine) as session:
                        images = session.exec(
                            select(Image).where(Image.generation_id == generation.id)
                        ).all()
                    self.vectors.verify(generation, images)
                    self._verified = True
                else:
                    self.vectors.check_collection(generation)
                    if (
                        self.vectors.client.count(
                            generation.collection, exact=True
                        ).count
                        != generation.count
                    ):
                        raise SearchError(
                            "The vector index is incomplete. Rebuild the collection.",
                            "index_inconsistent",
                        )
            except SearchError:
                raise
            except Exception:  # noqa: BLE001 -- sanitize provider failures at the service boundary
                raise SearchError(
                    "The Qdrant vector database is unavailable. Browsing is still available.",
                    "qdrant_unavailable",
                ) from None
        return generation

    def status(self) -> StatusResponse:
        try:
            generation = self.generation()
        except SearchError as error:
            return StatusResponse(status="empty", message=str(error))
        try:
            self.generation(require_vectors=True)
        except SearchError as error:
            return StatusResponse(
                status="unavailable",
                index_version=generation.id,
                indexed_images=generation.count,
                message=str(error),
            )
        return StatusResponse(
            status="ready",
            index_version=generation.id,
            indexed_images=generation.count,
            search_available=True,
            text_search_available=bool(self.settings.gemini_api_key),
            message="Your collection is ready to explore.",
        )

    @logfire.instrument("catalogue.read_images", extract_args=False)
    def read_images(
        self, generation: Generation, ids: list[str]
    ) -> dict[str, ImageRead]:
        if not ids:
            return {}
        with Session(self.engine) as session:
            images = session.exec(
                select(Image).where(
                    Image.generation_id == generation.id, Image.image_id.in_(ids)
                )
            ).all()
            associations = session.exec(
                select(Association)
                .where(
                    Association.generation_id == generation.id,
                    Association.image_id.in_(ids),
                )
                .order_by(Association.id)
            ).all()
        by_image = {image.image_id: [] for image in images}
        for association in associations:
            route = (
                "objects" if association.record_uid.startswith("co") else "documents"
            )
            by_image[association.image_id].append(
                AssociationRead(
                    **association.model_dump(
                        exclude={"id", "generation_id", "image_id", "source_json"}
                    ),
                    source_url=f"https://collection.sciencemuseumgroup.org.uk/{route}/{association.record_uid}",
                )
            )
        return {
            image.image_id: ImageRead(
                image_id=image.image_id,
                title=image.title,
                image_url=f"/api/v1/images/{image.image_id}/file",
                width=image.width,
                height=image.height,
                associations=by_image[image.image_id],
            )
            for image in images
        }

    def image(self, image_id: str) -> ImageRead:
        generation = self.generation()
        result = self.read_images(generation, [image_id]).get(image_id)
        if not result:
            raise SearchError(
                "This image is not part of the current collection.",
                "image_missing",
                404,
            )
        return result

    def image_path(self, image_id: str):
        generation = self.generation()
        with Session(self.engine) as session:
            image = session.get(Image, (generation.id, image_id))
        if not image:
            raise SearchError(
                "This image is not part of the current collection.",
                "image_missing",
                404,
            )
        return safe_path(self.settings, image.relative_path), image.mime_type

    @logfire.instrument("catalogue.filter_options", extract_args=False)
    def filter_options(self) -> FilterOptions:
        generation = self.generation()
        with Session(self.engine) as session:
            associations = session.exec(
                select(Association).where(Association.generation_id == generation.id)
            ).all()
        ranges = [interval for item in associations for interval in item.date_ranges]
        return FilterOptions(
            index_version=generation.id,
            places=labels(value for item in associations for value in item.places),
            categories=labels(
                value for item in associations for value in item.categories
            ),
            date_min=min((item["date_from"] for item in ranges), default=None),
            date_max=max((item["date_to"] for item in ranges), default=None),
        )

    @logfire.instrument("catalogue.browse", extract_args=False)
    def browse(
        self,
        limit: int,
        cursor: str | None = None,
        filters: MetadataFilters | None = None,
    ) -> BrowseResponse:
        generation = self.generation()
        filters = filters or MetadataFilters()
        filter_hash = hashlib.sha256(filters.model_dump_json().encode()).hexdigest()[
            :16
        ]
        last_id = ""
        if cursor:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor))
                version, last_id = decoded["version"], decoded["last_id"]
                if not isinstance(last_id, str):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                raise SearchError(
                    "This browsing cursor is invalid.", "invalid_cursor", 422
                ) from None
            if version != generation.id:
                raise SearchError(
                    "The collection has changed. Start browsing again.",
                    "index_changed",
                    409,
                )
            if decoded.get("filters") != filter_hash:
                raise SearchError(
                    "Filters have changed. Start browsing again.",
                    "filters_changed",
                    409,
                )
        with Session(self.engine) as session:
            predicate = catalogue_filter(filters)
            matching = session.exec(
                select(func.count())
                .select_from(Image)
                .where(Image.generation_id == generation.id, predicate)
            ).one()
            ids = list(
                session.exec(
                    select(Image.image_id)
                    .where(
                        Image.generation_id == generation.id,
                        Image.image_id > last_id,
                        predicate,
                    )
                    .order_by(Image.image_id)
                    .limit(limit + 1)
                ).all()
            )
        next_cursor = None
        if len(ids) > limit:
            ids = ids[:limit]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "version": generation.id,
                        "last_id": ids[-1],
                        "filters": filter_hash,
                    }
                ).encode()
            ).decode()
        images = self.read_images(generation, ids)
        return BrowseResponse(
            index_version=generation.id,
            indexed_images=generation.count,
            matching_images=matching,
            items=[images[key] for key in ids],
            next_cursor=next_cursor,
        )

    @contextmanager
    def search_slot(self):
        if not self._slots.acquire(blocking=False):
            raise SearchError(
                "Search is busy. Please try again in a moment.", "search_busy", 429
            )
        try:
            yield
        finally:
            self._slots.release()

    @logfire.instrument("search", extract_args=False)
    def search(
        self,
        *,
        limit: int = 24,
        text: str | None = None,
        image: bytes | None = None,
        mime_type: str = "image/jpeg",
        similar_id: str | None = None,
        filters: MetadataFilters | None = None,
    ) -> SearchResponse:
        started = perf_counter()
        with self.search_slot():
            generation = self.generation(require_vectors=True)
            if similar_id:
                self.image(similar_id)
            try:
                vector = (
                    self.vectors.vector(generation, similar_id)
                    if similar_id
                    else self.embeddings.embed(
                        text=text, image=image, mime_type=mime_type
                    )
                )
                points = self.vectors.search(
                    generation, vector, limit, exclude=similar_id, filters=filters
                )
            except SearchError:
                raise
            except Exception:  # noqa: BLE001 -- return a safe error for any vector transport failure
                raise SearchError(
                    "The vector search could not complete. Try again.",
                    "qdrant_unavailable",
                ) from None
            images = self.read_images(generation, [str(point.id) for point in points])
            results = []
            for point in points:
                item = images.get(str(point.id))
                if item is None or not item.associations:
                    raise SearchError(
                        "The vector index and catalogue are inconsistent.",
                        "index_inconsistent",
                    )
                item.score = point.score
                results.append(item)
        return SearchResponse(
            index_version=generation.id,
            indexed_images=generation.count,
            duration_ms=round((perf_counter() - started) * 1000),
            results=results,
        )
