import hashlib
import json
import time
from uuid import uuid4

from sqlmodel import Session, select

from app.core.config import Settings
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
from app.services.selection import safe_path


def create_generation(
    engine, settings: Settings, selected: list[dict], report: dict
) -> str:
    if not selected:
        raise ValueError("No eligible local images were found within the scan limit.")
    generation_id = uuid4().hex[:16]
    generation = Generation(
        id=generation_id,
        collection=f"{settings.qdrant_collection_prefix}_{generation_id}",
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        config_hash=settings.config_hash,
        count=len(selected),
        scanned=report["scanned"],
        skipped=sum(report["skipped"].values()),
    )
    snapshot = settings.data_dir / "selections" / f"{generation_id}.jsonl"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in selected)
    )
    with Session(engine, expire_on_commit=False) as session:
        session.add(generation)
        session.flush()
        for item in selected:
            values = {key: item[key] for key in Image.model_fields if key in item}
            values["filter_metadata"] = filter_rows(item["associations"])
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
    return generation_id


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
    try:
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
                    with Session(engine, expire_on_commit=False) as session:
                        cache = session.get(EmbeddingCache, cache_key)
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
                        with Session(engine, expire_on_commit=False) as session:
                            session.add(
                                EmbeddingCache(
                                    key=cache_key,
                                    checksum=image.checksum,
                                    config_hash=generation.config_hash,
                                    vector=vector,
                                )
                            )
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
