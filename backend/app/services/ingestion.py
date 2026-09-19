import hashlib
import json
import time
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


@logfire.instrument("index.run", extract_args=["generation_id"])
def run_ingestion(
    engine, settings: Settings, generation_id: str, embeddings, vectors, progress=print
) -> dict:
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
        for position, image in enumerate(images, 1):
            cache_key = hashlib.sha256(
                f"{image.checksum}:{generation.config_hash}".encode()
            ).hexdigest()
            with Session(engine, expire_on_commit=False) as session:
                tracking = session.get(Ingestion, (generation_id, image.image_id))
            if tracking.status in {"indexed", "upserting"} and vectors.matches(
                generation, image
            ):
                with Session(engine, expire_on_commit=False) as session:
                    tracking = session.get(Ingestion, (generation_id, image.image_id))
                    tracking.status, tracking.indexed_at, tracking.last_error = (
                        "indexed",
                        now(),
                        None,
                    )
                    session.add(tracking)
                    session.commit()
                    reused += 1
                    progress(f"[{position}/{len(images)}] verified {image.title}")
                    continue
            for attempt in range(3):
                try:
                    with logfire.span(
                        "index.image",
                        run_id=generation_id,
                        image_id=image.image_id,
                        attempt=attempt + 1,
                    ):
                        with Session(cache_engine) as cache_session:
                            cache = cache_session.get(EmbeddingCache, cache_key)
                        with Session(engine, expire_on_commit=False) as session:
                            tracking = session.get(
                                Ingestion, (generation_id, image.image_id)
                            )
                            tracking.attempts += 1
                            tracking.status = "embedded" if cache else "embedding"
                            tracking.updated_at = now()
                            session.add(tracking)
                            session.commit()
                        if cache:
                            vector = normalize(cache.vector, generation.dimensions)
                        else:
                            path = safe_path(settings, image.relative_path)
                            with path.open("rb") as stream:
                                data = stream.read(settings.max_image_bytes + 1)
                            if hashlib.sha256(data).hexdigest() != image.checksum:
                                raise SearchError(
                                    "A selected image changed since sampling.",
                                    "source_changed",
                                )
                            vector = normalize(
                                embeddings.embed(image=data, mime_type=image.mime_type),
                                generation.dimensions,
                            )
                            with Session(cache_engine) as cache_session:
                                cache_session.add(
                                    EmbeddingCache(
                                        key=cache_key,
                                        checksum=image.checksum,
                                        config_hash=generation.config_hash,
                                        vector=vector,
                                    )
                                )
                                cache_session.commit()
                            with Session(engine, expire_on_commit=False) as session:
                                tracking = session.get(
                                    Ingestion, (generation_id, image.image_id)
                                )
                                tracking.status, tracking.updated_at = "embedded", now()
                                session.add(tracking)
                                session.commit()
                            embedded += 1
                        with Session(engine, expire_on_commit=False) as session:
                            tracking = session.get(
                                Ingestion, (generation_id, image.image_id)
                            )
                            tracking.status, tracking.updated_at = "upserting", now()
                            session.add(tracking)
                            session.commit()
                        vectors.upsert(generation, image, vector)
                        with Session(engine, expire_on_commit=False) as session:
                            tracking = session.get(
                                Ingestion, (generation_id, image.image_id)
                            )
                            tracking.status, tracking.indexed_at = "indexed", now()
                            tracking.updated_at, tracking.last_error = now(), None
                            session.add(tracking)
                            session.commit()
                        if cache:
                            reused += 1
                        progress(f"[{position}/{len(images)}] indexed {image.title}")
                        break
                except Exception as error:
                    logfire.warn(
                        "Image ingestion attempt failed",
                        run_id=generation_id,
                        image_id=image.image_id,
                        attempt=attempt + 1,
                        error_type=type(error).__name__,
                    )
                    message = (
                        str(error)
                        if isinstance(error, (SearchError, ValueError))
                        else type(error).__name__
                    )
                    with Session(engine, expire_on_commit=False) as session:
                        tracking = session.get(
                            Ingestion, (generation_id, image.image_id)
                        )
                        tracking.status, tracking.last_error, tracking.updated_at = (
                            "failed",
                            message,
                            now(),
                        )
                        session.add(tracking)
                        session.commit()
                    permanent = isinstance(error, SearchError) and error.code in {
                        "missing_api_key",
                        "embedding_configuration",
                        "source_changed",
                        "invalid_embedding",
                        "index_mismatch",
                    }
                    if permanent or attempt == 2:
                        raise
                    time.sleep(2**attempt)
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
