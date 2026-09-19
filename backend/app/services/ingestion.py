import hashlib
import json
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
from contextvars import copy_context
from itertools import islice
from threading import Event, Lock
from uuid import uuid4

import logfire
from sqlmodel import Session, select

from app.core.config import Settings
from app.core.db import make_cache_engine
from app.models import (
    Association,
    EmbeddingCache,
    Generation,
    Image,
    Ingestion,
    ServiceState,
    now,
)
from app.services.embeddings import SearchError, normalize
from app.services.metadata import filter_rows
from app.services.r2_catalogue import r2_object_url
from app.services.selection import safe_path


def image_values(settings, item):
    values = {
        key: item[key] for key in Image.model_fields if key in item and key != "r2_url"
    }
    values["filter_metadata"] = filter_rows(item["associations"])
    if settings.r2_endpoint_url:
        values["r2_url"] = r2_object_url(settings, item["relative_path"])
    return values


def saved_generation_id(engine, settings: Settings) -> str:
    """Resolve the configured collection or a sole saved generation without sampling."""
    with Session(engine) as session:
        query = select(Generation.id)
        if settings.qdrant_collection_name:
            query = query.where(
                Generation.collection == settings.qdrant_collection_name
            )
        generation_ids = session.exec(query.limit(2)).all()
    if not generation_ids:
        raise ValueError(
            "No saved selection was found for this collection. "
            "Select images first with --limit 50 (make index LIMIT=50)."
        )
    if len(generation_ids) > 1:
        raise ValueError(
            "Multiple saved selections were found. Specify --resume RUN_ID "
            "or set QDRANT_COLLECTION_NAME to the collection to reuse."
        )
    return generation_ids[0]


def create_generation(
    engine, settings: Settings, selected: list[dict], report: dict
) -> str:
    if not selected:
        raise ValueError("No eligible local images were found within the scan limit.")
    if settings.qdrant_collection_name:
        with Session(engine) as session:
            existing = session.exec(
                select(Generation).where(
                    Generation.collection == settings.qdrant_collection_name
                )
            ).first()
            if existing:
                if existing.config_hash != settings.config_hash:
                    raise ValueError(
                        "Permanent collection requires the original embedding settings."
                    )
                return extend_generation(engine, settings, existing.id, selected)
    generation_id = uuid4().hex[:16]
    generation = Generation(
        id=generation_id,
        collection=settings.qdrant_collection_name
        or f"{settings.qdrant_collection_prefix}_{generation_id}",
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        config_hash=settings.config_hash,
        count=len(selected),
        scanned=report["scanned"],
        skipped=sum(report["skipped"].values()),
    )
    with Session(engine, expire_on_commit=False) as session:
        session.add(generation)
        session.flush()
        for item in selected:
            values = image_values(settings, item)
            image = Image(generation_id=generation_id, **values)
            session.add(image)
            session.flush()
            for index, association in enumerate(item["associations"]):
                session.add(
                    Association(
                        id=f"{generation_id}:{image.image_id}:{index}",
                        generation_id=generation_id,
                        image_id=image.image_id,
                        **association,
                    )
                )
            session.add(
                Ingestion(
                    generation_id=generation_id,
                    image_id=image.image_id,
                    checksum=image.checksum,
                    config_hash=generation.config_hash,
                    point_id=image.image_id,
                    collection=generation.collection,
                )
            )
        session.commit()
    save_selection(engine, settings, generation_id)
    return generation_id


def extend_generation(engine, settings, generation_id, selected):
    """Upsert selected catalogue rows and retain all previously selected images."""
    with Session(engine) as session:
        generation = session.get(Generation, generation_id)
        generation.status, generation.error, generation.completed_at = (
            "building",
            None,
            None,
        )
        session.add(generation)
        for item in selected:
            image = session.get(Image, (generation_id, item["image_id"]))
            values = image_values(settings, item)
            if image is None:
                image = Image(generation_id=generation_id, **values)
            else:
                if any(
                    values.get(key, getattr(image, key)) != getattr(image, key)
                    for key in ("relative_path", "checksum")
                ):
                    image.r2_url = None
                for key, value in values.items():
                    setattr(image, key, value)
            session.add(image)
            session.flush()
            for association in session.exec(
                select(Association).where(
                    Association.generation_id == generation_id,
                    Association.image_id == image.image_id,
                )
            ).all():
                session.delete(association)
            session.flush()
            for index, association in enumerate(item["associations"]):
                session.add(
                    Association(
                        id=f"{generation_id}:{image.image_id}:{index}",
                        generation_id=generation_id,
                        image_id=image.image_id,
                        **association,
                    )
                )
            tracking = session.get(Ingestion, (generation_id, image.image_id))
            if tracking is None:
                tracking = Ingestion(
                    generation_id=generation_id,
                    image_id=image.image_id,
                    checksum=image.checksum,
                    config_hash=generation.config_hash,
                    collection=generation.collection,
                    point_id=image.image_id,
                )
            tracking.checksum = image.checksum
            session.add(tracking)
        session.flush()
        images = session.exec(
            select(Image).where(Image.generation_id == generation_id)
        ).all()
        generation.count = len(images)
        session.add(generation)
        session.commit()
    save_selection(engine, settings, generation_id)
    return generation_id


def save_selection(engine, settings, generation_id):
    rows = []
    with Session(engine) as session:
        for image in session.exec(
            select(Image)
            .where(Image.generation_id == generation_id)
            .order_by(Image.image_id)
        ).all():
            row = image.model_dump(exclude={"generation_id"})
            row["associations"] = [
                association.model_dump(exclude={"id", "generation_id", "image_id"})
                for association in session.exec(
                    select(Association).where(
                        Association.generation_id == generation_id,
                        Association.image_id == image.image_id,
                    )
                ).all()
            ]
            rows.append(row)
    path = settings.data_dir / "selections" / f"{generation_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )
    temporary.replace(path)


# Keep inline requests comfortably below the provider's encoded request limit.
MAX_BATCH_IMAGE_BYTES = 12 * 1024 * 1024


def _cache_key(generation, image):
    return hashlib.sha256(
        f"{image.checksum}:{generation.config_hash}".encode()
    ).hexdigest()


def _image_payloads(settings, images):
    """Read only one bounded request's images, splitting large batches by bytes."""
    payloads = []
    size = 0
    for image in images:
        path = safe_path(settings, image.relative_path)
        with path.open("rb") as stream:
            data = stream.read(min(settings.max_image_bytes, MAX_BATCH_IMAGE_BYTES) + 1)
        if len(data) > min(settings.max_image_bytes, MAX_BATCH_IMAGE_BYTES):
            raise SearchError(
                "A selected image exceeds the indexing byte limit.", "source_changed"
            )
        if hashlib.sha256(data).hexdigest() != image.checksum:
            raise SearchError(
                "A selected image changed since sampling.", "source_changed"
            )
        if payloads and size + len(data) > MAX_BATCH_IMAGE_BYTES:
            yield payloads
            payloads = []
            size = 0
        payloads.append((image, data))
        size += len(data)
    if payloads:
        yield payloads


def _set_ingestion_status(engine, generation_id, images, status, error=None):
    with Session(engine, expire_on_commit=False) as session:
        for image in images:
            tracking = session.get(Ingestion, (generation_id, image.image_id))
            tracking.status, tracking.updated_at = status, now()
            tracking.last_error = error
            if status == "indexed":
                tracking.indexed_at = now()
            session.add(tracking)
        session.commit()


def _ingest_batch(
    engine,
    cache_engine,
    settings,
    generation,
    images,
    embeddings,
    vectors,
    vector_lock,
    stopped,
):
    """Embed uncached content together, then checkpoint each image's vector write."""
    generation_id = generation.id
    remaining = {}
    results = []
    embedded_ids = set()
    for image in images:
        with Session(engine) as session:
            tracking = session.get(Ingestion, (generation_id, image.image_id))
            status = tracking.status
        with vector_lock:
            matched = status in {"indexed", "upserting"} and vectors.matches(
                generation, image
            )
        if matched:
            _set_ingestion_status(engine, generation_id, [image], "indexed")
            results.append((image, 0, 1, "verified"))
        else:
            remaining[image.image_id] = image
    if not remaining:
        return results
    for attempt in range(3):
        try:
            with logfire.span(
                "index.batch",
                run_id=generation_id,
                batch_size=len(remaining),
                attempt=attempt + 1,
            ):
                # Each checksum is embedded once, including duplicate content
                # within this batch and content already cached by another worker.
                unique = {}
                for image in remaining.values():
                    unique.setdefault(image.checksum, image)
                cached = {}
                with Session(cache_engine) as session:
                    for checksum, image in unique.items():
                        entry = session.get(
                            EmbeddingCache, _cache_key(generation, image)
                        )
                        if entry:
                            cached[checksum] = normalize(
                                entry.vector, generation.dimensions
                            )
                with Session(engine, expire_on_commit=False) as session:
                    for image in remaining.values():
                        tracking = session.get(
                            Ingestion, (generation_id, image.image_id)
                        )
                        tracking.attempts += 1
                        tracking.status = (
                            "embedded" if image.checksum in cached else "embedding"
                        )
                        tracking.updated_at = now()
                        session.add(tracking)
                    session.commit()
                missing = [
                    image
                    for checksum, image in unique.items()
                    if checksum not in cached
                ]
                for payloads in _image_payloads(settings, missing):
                    batch_vectors = embeddings.embed_images(
                        [(data, image.mime_type) for image, data in payloads]
                    )
                    if len(batch_vectors) != len(payloads):
                        raise SearchError(
                            "Gemini returned an unexpected number of embeddings.",
                            "invalid_embedding",
                        )
                    # Validate the whole response before pairing or caching any vector.
                    batch_vectors = [
                        normalize(vector, generation.dimensions)
                        for vector in batch_vectors
                    ]
                    with Session(cache_engine) as session:
                        for (image, _), vector in zip(
                            payloads, batch_vectors, strict=True
                        ):
                            session.add(
                                EmbeddingCache(
                                    key=_cache_key(generation, image),
                                    checksum=image.checksum,
                                    config_hash=generation.config_hash,
                                    vector=vector,
                                )
                            )
                        session.commit()
                    for (image, _), vector in zip(payloads, batch_vectors, strict=True):
                        cached[image.checksum] = vector
                        embedded_ids.add(image.image_id)
                    _set_ingestion_status(
                        engine,
                        generation_id,
                        [
                            image
                            for image in remaining.values()
                            if image.checksum in cached
                        ],
                        "embedded",
                    )
                break
        except Exception as error:
            _record_failure(engine, generation_id, remaining.values(), error, attempt)
            if _permanent_error(error) or attempt == 2 or stopped.is_set():
                raise
            time.sleep(2**attempt)

    for image in remaining.values():
        _index_cached_image(
            engine,
            generation,
            image,
            cached[image.checksum],
            vectors,
            vector_lock,
            stopped,
            first_attempt=attempt,
        )
        new_embedding = int(image.image_id in embedded_ids)
        results.append((image, new_embedding, 1 - new_embedding, "indexed"))
    return results


def _index_cached_image(
    engine,
    generation,
    image,
    vector,
    vectors,
    vector_lock,
    stopped,
    *,
    first_attempt,
):
    # The successful embedding/cache attempt also covers the first vector write.
    for attempt in range(first_attempt, 3):
        try:
            with logfire.span(
                "index.image",
                run_id=generation.id,
                image_id=image.image_id,
                attempt=attempt + 1,
            ):
                with Session(engine, expire_on_commit=False) as session:
                    tracking = session.get(Ingestion, (generation.id, image.image_id))
                    if attempt > first_attempt:
                        tracking.attempts += 1
                    tracking.status, tracking.updated_at = "upserting", now()
                    session.add(tracking)
                    session.commit()
                with vector_lock:
                    vectors.upsert(generation, image, vector)
                _set_ingestion_status(engine, generation.id, [image], "indexed")
                return
        except Exception as error:
            _record_failure(engine, generation.id, [image], error, attempt)
            if _permanent_error(error) or attempt == 2 or stopped.is_set():
                raise
            time.sleep(2**attempt)


def _record_failure(engine, generation_id, images, error, attempt):
    images = list(images)
    logfire.warn(
        "Image ingestion attempt failed",
        run_id=generation_id,
        batch_size=len(images),
        attempt=attempt + 1,
        error_type=type(error).__name__,
    )
    message = (
        str(error)
        if isinstance(error, (SearchError, ValueError))
        else type(error).__name__
    )
    _set_ingestion_status(engine, generation_id, images, "failed", message)


def _permanent_error(error):
    return isinstance(error, SearchError) and error.code in {
        "missing_api_key",
        "embedding_configuration",
        "source_changed",
        "invalid_embedding",
        "index_mismatch",
    }


@logfire.instrument("index.run", extract_args=["generation_id"])
def run_ingestion(
    engine,
    settings: Settings,
    generation_id: str,
    embeddings,
    vectors,
    progress=print,
    *,
    workers: int = 10,
    batch_size: int = 10,
) -> dict:
    if not 1 <= workers <= 10:
        raise ValueError("workers must be 1–10")
    if not 1 <= batch_size <= 100:
        raise ValueError("batch-size must be 1–100")
    with Session(engine, expire_on_commit=False) as session:
        generation = session.get(Generation, generation_id)
        if not generation:
            raise ValueError("Unknown ingestion run.")
        if generation.config_hash != settings.config_hash:
            raise ValueError(
                "Resume requires the original embedding model and dimensions."
            )
        images = session.exec(
            select(Image)
            .where(Image.generation_id == generation_id)
            .order_by(Image.image_id)
        ).all()
        urls_changed = False
        if settings.r2_endpoint_url:
            for image in images:
                url = r2_object_url(settings, image.relative_path)
                if image.r2_url != url:
                    image.r2_url = url
                    session.add(image)
                    urls_changed = True
            if urls_changed:
                session.commit()
                save_selection(engine, settings, generation_id)
        if generation.status == "ready":
            vectors.verify(generation, images)
            return {
                "index_version": generation.id,
                "indexed": len(images),
                "embedded": 0,
                "reused": len(images),
            }
        generation.status, generation.error = "building", None
        session.add(generation)
        session.commit()
        session.refresh(generation)
    embedded = reused = 0
    cache_engine = None
    try:
        cache_engine = make_cache_engine(settings)
        save_selection(engine, settings, generation_id)
        vectors.ensure_collection(generation)
        cache_locks = {image.checksum: Lock() for image in images}
        vector_lock = Lock()
        stopped = Event()

        def process(batch):
            try:
                # Identical content shares one cache entry, even at different paths.
                with ExitStack() as locks:
                    # A stable lock order avoids deadlocks between overlapping batches.
                    for checksum in sorted({image.checksum for image in batch}):
                        locks.enter_context(cache_locks[checksum])
                    if stopped.is_set():
                        return None
                    return _ingest_batch(
                        engine,
                        cache_engine,
                        settings,
                        generation,
                        batch,
                        embeddings,
                        vectors,
                        vector_lock,
                        stopped,
                    )
            except BaseException:
                stopped.set()
                raise

        remaining = iter(images)
        pending = set()
        completed = 0
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="index")
        try:

            def fill_workers():
                while len(pending) < workers and not stopped.is_set():
                    batch = list(islice(remaining, batch_size))
                    if not batch:
                        break
                    future = executor.submit(copy_context().run, process, batch)
                    pending.add(future)

            fill_workers()
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    pending.remove(future)
                    result = future.result()
                    if result is None:
                        continue
                    for image, new_embeddings, cache_hits, action in result:
                        embedded += new_embeddings
                        reused += cache_hits
                        completed += 1
                        progress(f"[{completed}/{len(images)}] {action} {image.title}")
                fill_workers()
        finally:
            stopped.set()
            # Keep clients, cache, and the writer lock alive until workers finish.
            executor.shutdown(wait=True, cancel_futures=True)
        vectors.verify(generation, images)
        report = {
            "index_version": generation_id,
            "indexed": len(images),
            "embedded": embedded,
            "reused": reused,
        }
        report_path = settings.data_dir / "runs" / f"{generation_id}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        with Session(engine, expire_on_commit=False) as session:
            generation = session.get(Generation, generation_id)
            generation.status, generation.completed_at = "ready", now()
            state = session.get(ServiceState, 1) or ServiceState(
                active_generation=generation_id
            )
            state.active_generation = generation_id
            session.add(generation)
            session.add(state)
            session.commit()
        logfire.info("Indexing completed", **report)
        return report
    except BaseException as error:
        with Session(engine, expire_on_commit=False) as session:
            generation = session.get(Generation, generation_id)
            if generation.status != "ready":
                generation.status = "failed"
                generation.error = (
                    str(error)
                    if isinstance(error, SearchError)
                    else type(error).__name__
                )
                session.add(generation)
                session.commit()
        raise
    finally:
        if cache_engine is not None:
            cache_engine.dispose()
