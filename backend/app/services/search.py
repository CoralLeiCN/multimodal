import base64
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from time import monotonic, perf_counter

import logfire

from app.core.config import Settings
from app.models import Generation
from app.schemas import (
    AssociationRead,
    BrowseResponse,
    FilterOptions,
    ImageRead,
    MetadataFilters,
    SearchResponse,
    StatusResponse,
)
from app.services.catalogue import Catalogue, PostgresQueries
from app.services.deadline import (
    check_deadline,
    remaining,
    search_deadline,
    timeout_error,
)
from app.services.embeddings import SearchError
from app.services.selection import safe_path

SEARCH_TIMEOUT_SECONDS = 30
STATUS_CHECK_WAIT_SECONDS = 0.1


class SearchService:
    def __init__(
        self, engine, settings: Settings, vectors, embeddings, *, catalogue=None
    ):
        self.engine, self.settings = engine, settings
        self.catalogue = catalogue or Catalogue(PostgresQueries(engine))
        self.vectors, self.embeddings = vectors, embeddings
        self._active = None
        self._verified = False
        self._slots = threading.BoundedSemaphore(4)
        self._verification_lock = threading.Lock()
        self._verification_future = None
        self._verification_key = None
        self._verification_cancel = threading.Event()
        self._verification_error = None
        self._closing = False
        self._verifier = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="index-verification"
        )
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="search")

    def generation(self, require_vectors=False) -> Generation:
        generation = self.catalogue.generation()
        if not generation or generation.status != "ready":
            with self._verification_lock:
                self._active, self._verified = None, False
                self._verification_cancel.set()
            raise SearchError(
                "The collection is being prepared. No image index is ready yet.",
                "index_empty",
            )
        if require_vectors:
            future = self._start_verification(generation)
            if future is None:
                raise SearchError(
                    "The collection is being checked. Please try again shortly.",
                    "index_verifying",
                )
            try:
                future.result(timeout=remaining())
            except TimeoutError:
                # The shared verification keeps running after this request expires.
                raise timeout_error() from None
            check_deadline()
            with self._verification_lock:
                if self._active != (generation.id, generation.completed_at):
                    raise SearchError(
                        "The collection changed. Retry the lookup.", "index_changed"
                    )
        return generation

    def _start_verification(self, generation):
        if (
            self.settings.qdrant_collection_name
            and self.settings.qdrant_collection_name != generation.collection
        ):
            raise SearchError(
                "The configured Qdrant collection does not match the active catalogue.",
                "index_mismatch",
            )
        if generation.config_hash != self.settings.config_hash:
            raise SearchError(
                "Backend embedding settings do not match the active collection.",
                "index_mismatch",
            )
        key = (generation.id, generation.completed_at)
        with self._verification_lock:
            if self._closing:
                raise SearchError("The search service is shutting down.")
            if self._active != key:
                self._verification_cancel.set()
                self._active = key
                self._verified, self._verification_error = False, None
            if self._verification_future and not self._verification_future.done():
                # Keep only one job, including while an older generation exits.
                return (
                    self._verification_future if self._verification_key == key else None
                )
            self._verification_key = key
            self._verification_cancel = threading.Event()
            self._verification_future = self._verifier.submit(
                copy_context().run,
                self._verify,
                generation,
                key,
                self._verification_cancel,
                not self._verified,
            )
            return self._verification_future

    def _verify(self, generation, key, cancelled, full):
        def check_cancelled():
            if cancelled.is_set():
                raise SearchError(
                    "The collection changed. Retry the lookup.", "index_changed"
                )

        def images():
            for image in self.catalogue.iter_images(generation.id):
                check_cancelled()
                yield image

        error = None
        try:
            # Preserve tracing context, but never inherit a caller's deadline.
            with search_deadline(None):
                check_cancelled()
                if full:
                    self.vectors.verify(generation, images())
                else:
                    self.vectors.check_collection(generation)
                    check_cancelled()
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
                check_cancelled()
        except SearchError as failure:
            error = failure
        except Exception:  # noqa: BLE001 -- sanitize provider failures at the service boundary
            error = SearchError(
                "The Qdrant vector database is unavailable. Browsing is still available.",
                "qdrant_unavailable",
            )
        with self._verification_lock:
            if self._active == key and not cancelled.is_set():
                self._verified = error is None
                self._verification_error = error
        if error is not None:
            raise error

    def status(self) -> StatusResponse:
        try:
            generation = self.generation()
        except SearchError as error:
            return StatusResponse(
                status="empty" if error.code == "index_empty" else "unavailable",
                message=str(error),
            )
        try:
            future = self._start_verification(generation)
            if future is not None:
                try:
                    future.result(timeout=STATUS_CHECK_WAIT_SECONDS)
                except TimeoutError:
                    pass
            with self._verification_lock:
                verified = self._verified and self._active == (
                    generation.id,
                    generation.completed_at,
                )
                error = self._verification_error
            if error is not None:
                raise error
        except SearchError as error:
            return StatusResponse(
                status="unavailable",
                index_version=generation.id,
                indexed_images=generation.count,
                message=str(error),
            )
        if not verified:
            return StatusResponse(
                status="checking",
                index_version=generation.id,
                indexed_images=generation.count,
                message="Search is being prepared. You can browse the collection now.",
            )
        return StatusResponse(
            status="ready",
            embedding_provider="local",
            index_version=generation.id,
            indexed_images=generation.count,
            search_available=True,
            text_search_available=True,
            message="Your collection is ready to explore.",
        )

    @logfire.instrument("catalogue.read_images", extract_args=False)
    def read_images(
        self, generation: Generation, ids: list[str]
    ) -> dict[str, ImageRead]:
        if not ids:
            return {}
        images = self.catalogue.images(generation.id, ids)
        associations = self.catalogue.associations(generation.id, ids)
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

    def lookup_record(self, record_uid: str):
        generation = self.generation()
        ids = self.catalogue.record_images(generation.id, record_uid)
        images = self.read_images(generation, ids)
        return {
            "index_version": generation.id,
            "record_uid": record_uid,
            "results": [images[key].model_dump(mode="json") for key in ids],
            "message": ""
            if ids
            else "No images for this record in the active indexed catalogue.",
        }

    def image_source(self, image_id: str):
        generation = self.generation()
        images = self.catalogue.images(generation.id, [image_id])
        image = images[0] if images else None
        if not image:
            raise SearchError(
                "This image is not part of the current collection.",
                "image_missing",
                404,
            )
        path = safe_path(self.settings, image.relative_path, must_exist=False)
        if path.is_file():
            return path, image.mime_type
        raise SearchError("This collection image is unavailable.", "image_missing", 404)

    @logfire.instrument("catalogue.filter_options", extract_args=False)
    def filter_options(self) -> FilterOptions:
        generation = self.generation()
        return FilterOptions(
            index_version=generation.id,
            **self.catalogue.filter_options(generation.id),
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
        matching, ids = self.catalogue.browse(generation.id, filters, last_id, limit)
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

    def close(self):
        with self._verification_lock:
            self._closing = True
            self._verification_cancel.set()
        # Stop verification between batches and drain native work before clients close.
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._verifier.shutdown(wait=True, cancel_futures=True)

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
        expires = monotonic() + SEARCH_TIMEOUT_SECONDS
        if not self._slots.acquire(blocking=False):
            raise SearchError(
                "Search is busy. Please try again in a moment.", "search_busy", 429
            )
        try:
            future = self._executor.submit(
                copy_context().run,
                self._search,
                expires,
                limit=limit,
                text=text,
                image=image,
                mime_type=mime_type,
                similar_id=similar_id,
                filters=filters,
            )
        except BaseException:
            self._slots.release()
            raise
        # A timeout cannot safely interrupt native model inference. Keep its
        # slot until it exits so repeated timeouts cannot create unbounded work.
        future.add_done_callback(lambda _: self._slots.release())
        try:
            return future.result(timeout=max(0, expires - monotonic()))
        except TimeoutError:
            future.cancel()
            raise timeout_error() from None

    def _search(self, expires, *, limit, text, image, mime_type, similar_id, filters):
        started = perf_counter()
        with search_deadline(expires):
            generation = self.generation(require_vectors=True)
            check_deadline()
            if similar_id:
                self.image(similar_id)
            check_deadline()
            try:
                vector = (
                    self.vectors.vector(generation, similar_id)
                    if similar_id
                    else self.embeddings.embed(
                        text=text, image=image, mime_type=mime_type
                    )
                )
                check_deadline()
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
            check_deadline()
            images = self.read_images(generation, [str(point.id) for point in points])
            check_deadline()
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
