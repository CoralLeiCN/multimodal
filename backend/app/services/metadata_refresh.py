"""Copy a prepared HF release's metadata into an existing application index."""

from itertools import islice

from qdrant_client import models
from sqlalchemy import delete
from sqlmodel import Session, select

from app.models import Association, Generation, Image
from app.services.dataset import CATALOGUE_VERSION, load_catalogue
from app.services.embeddings import SearchError
from app.services.ingestion import save_selection


def refresh_metadata(engine, settings, generation_id: str, scan_limit: int, vectors):
    with Session(engine, expire_on_commit=False) as session:
        generation = session.get(Generation, generation_id)
        if not generation:
            raise ValueError("Unknown ingestion run.")
        images = session.exec(
            select(Image).where(Image.generation_id == generation_id)
        ).all()
        wanted = {image.image_id: image for image in images}
        rows, release = load_catalogue(settings, with_images=False)
        found, scanned = {}, 0
        for row in islice(rows, scan_limit):
            scanned += 1
            if row.get("schema_version") != CATALOGUE_VERSION:
                raise ValueError("Unsupported catalogue schema")
            if row["image_id"] in wanted:
                if (
                    row["image_id"] in found
                    or row["checksum"] != wanted[row["image_id"]].checksum
                ):
                    raise ValueError(
                        "Dataset image identity changed; rebuild the index"
                    )
                found[row["image_id"]] = row
            if found.keys() >= wanted.keys():
                break
        if found.keys() != wanted.keys():
            raise ValueError(
                "Selected images were not found within the scan limit; nothing was changed. Use the matching release or increase --scan-limit."
            )
        if generation.status == "ready" and not vectors.client.collection_exists(
            generation.collection
        ):
            raise SearchError(
                "The ready vector collection is missing; rebuild a generation using cached embeddings.",
                "index_inconsistent",
            )
        vectors.ensure_collection(generation)
        for image in images:
            prepared = found[image.image_id]
            image.title = prepared["title"]
            image.filter_metadata = prepared["filter_metadata"]
            session.add(image)
            session.execute(
                delete(Association).where(
                    Association.generation_id == generation_id,
                    Association.image_id == image.image_id,
                )
            )
            for index, association in enumerate(prepared["associations"]):
                session.add(
                    Association(
                        id=f"{generation_id}:{image.image_id}:{index}",
                        generation_id=generation_id,
                        image_id=image.image_id,
                        **association,
                    )
                )
        generation.dataset_repo_id = release["repo_id"]
        generation.dataset_revision = release["revision"]
        generation.dataset_schema_version = release["schema_version"]
        session.add(generation)
        session.commit()
    save_selection(engine, settings, generation_id)
    # Catalogue metadata is the desired state. A retry repairs interrupted writes.
    updated = 0
    for image in images:
        points = vectors.client.retrieve(
            generation.collection,
            [image.image_id],
            with_payload=False,
            with_vectors=False,
        )
        if not points:
            continue
        result = vectors.client.overwrite_payload(
            generation.collection,
            payload=vectors.expected_payload(generation, image),
            points=[image.image_id],
            wait=True,
        )
        if result.status != models.UpdateStatus.COMPLETED or not vectors.matches(
            generation, image
        ):
            raise SearchError(
                "Qdrant did not confirm the metadata write. Retry the refresh.",
                "vector_write_failed",
            )
        updated += 1
    if generation.status == "ready":
        vectors.verify(generation, images)
    return {
        "index_version": generation_id,
        "scanned": scanned,
        "images": len(images),
        "payloads_updated": updated,
        "payload_indexes": 4,
        "dataset": release,
    }
