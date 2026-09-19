"""Refresh selected catalogue metadata without reading images or embedding them."""

import json
from itertools import islice
from pathlib import Path

from qdrant_client import models
from sqlmodel import Session, select

from app.models import Association, Generation, Image
from app.services.embeddings import SearchError
from app.services.metadata import extract_metadata, filter_rows
from app.services.selection import source


def refresh_metadata(
    engine, settings, generation_id: str, metadata: list[Path], scan_limit: int, vectors
):
    with Session(engine, expire_on_commit=False) as session:
        generation = session.get(Generation, generation_id)
        if not generation:
            raise ValueError("Unknown ingestion run.")
        associations = session.exec(
            select(Association).where(Association.generation_id == generation_id)
        ).all()
        images = session.exec(
            select(Image).where(Image.generation_id == generation_id)
        ).all()
        wanted = {(item.source_json, item.record_uid) for item in associations}
        found, scanned = {}, 0
        if len({path.name for path in metadata}) != len(metadata):
            raise ValueError("Metadata files must have distinct filenames.")
        for path in metadata:
            for record in islice(
                source.iter_records(path), max(0, scan_limit - scanned)
            ):
                scanned += 1
                key = (path.name, (record.get("@admin") or {}).get("uid"))
                if key in wanted:
                    found[key] = {
                        **extract_metadata(record),
                        "date": source.record_metadata(record)["date"],
                    }
                if found.keys() >= wanted:
                    break
            if found.keys() >= wanted:
                break
        if missing := wanted - found.keys():
            raise ValueError(
                f"{len(missing)} selected records were not found within the scan limit; nothing was changed. Supply the original metadata files or increase --scan-limit."
            )
        snapshot = settings.data_dir / "selections" / f"{generation_id}.jsonl"
        selected = [
            json.loads(line)
            for line in snapshot.read_text().splitlines()
            if line.strip()
        ]
        # Validate the snapshot before changing either database.
        if {item["image_id"] for item in selected} != {
            image.image_id for image in images
        }:
            raise ValueError("The selection snapshot does not match this generation.")
        for item in selected:
            for association in item["associations"]:
                association.update(
                    found[(association["source_json"], association["record_uid"])]
                )
            item["filter_metadata"] = filter_rows(item["associations"])
        if generation.status == "ready" and not vectors.client.collection_exists(
            generation.collection
        ):
            raise SearchError(
                "The ready vector collection is missing; rebuild a generation using cached embeddings.",
                "index_inconsistent",
            )
        vectors.ensure_collection(generation)
        for association in associations:
            for key, value in found[
                (association.source_json, association.record_uid)
            ].items():
                setattr(association, key, value)
            session.add(association)
        for image in images:
            image.filter_metadata = filter_rows(
                [
                    association.model_dump()
                    for association in associations
                    if association.image_id == image.image_id
                ]
            )
            session.add(image)
        session.commit()
        temporary = snapshot.with_suffix(".jsonl.tmp")
        temporary.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in selected)
        )
        temporary.replace(snapshot)

    # The Neon catalogue is the desired state. Retrying repairs an interrupted payload update.
    updated = 0
    for image in images:
        points = vectors.client.retrieve(
            generation.collection,
            [image.image_id],
            with_payload=False,
            with_vectors=False,
        )
        if not points:
            continue  # Prepared samples have no vectors yet.
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
    }
