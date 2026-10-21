"""Load the prepared SMG Hugging Face catalogue and cache original image bytes."""

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from itertools import islice
from pathlib import Path, PurePosixPath

from app.services.image_files import safe_path

CATALOGUE_VERSION = "1.0.0"
PACKAGE_VERSION = "3.0.0"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_catalogue(settings, *, with_images=True):
    from datasets import Image, IterableDataset
    from huggingface_hub import snapshot_download

    if settings.hf_dataset_path:
        root = settings.absolute(settings.hf_dataset_path)
        repository = f"local:{root}"
        revision = digest(root / "build_manifest.json")
    else:
        repository, revision = settings.hf_dataset_id, settings.hf_dataset_revision
        if (
            not repository
            or repository.startswith("YOUR_")
            or not revision
            or not re.fullmatch(r"[a-f0-9]{40}", revision)
        ):
            raise ValueError(
                "Set HF_DATASET_ID and HF_DATASET_REVISION to the published dataset and its full commit SHA, or set HF_DATASET_PATH for a local package."
            )
        root = Path(
            snapshot_download(
                repository,
                repo_type="dataset",
                revision=revision,
                allow_patterns=["build_manifest.json", "catalogue/*.parquet"],
            )
        )
    manifest = json.loads((root / "build_manifest.json").read_text())
    if manifest.get("schema_version") != PACKAGE_VERSION:
        raise ValueError(
            "Unsupported SMG package; rebuild it with smg-datasets schema 3.0.0"
        )
    entries = [
        entry for entry in manifest["files"] if entry["path"].startswith("catalogue/")
    ]
    paths = []
    for entry in entries:
        relative = PurePosixPath(entry["path"])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in str(relative)
            or relative.as_posix() != entry["path"]
            or relative.suffix != ".parquet"
        ):
            raise ValueError("Invalid catalogue shard path")
        path = root / relative
        if (
            not path.is_file()
            or path.stat().st_size != entry["bytes"]
            or digest(path) != entry["sha256"]
        ):
            raise ValueError(
                f"Catalogue shard does not match the release manifest: {relative}"
            )
        paths.append(str(path))
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("Missing or duplicate catalogue shards")
    # The explicit local reader avoids Hub discovery/telemetry after download,
    # so a local release also works with all network access disabled.
    rows = IterableDataset.from_parquet(sorted(paths), split="train", batch_size=256)
    rows = (
        rows.cast_column("image", Image(decode=False))
        if with_images
        else rows.remove_columns("image")
    )
    return rows, {
        "repo_id": repository,
        "revision": revision,
        "schema_version": CATALOGUE_VERSION,
    }


def cache_image(settings, row):
    checksum = row["checksum"]
    extension = {"image/jpeg": "jpg", "image/png": "png"}.get(row["mime_type"])
    if (
        not extension
        or not re.fullmatch(r"[a-f0-9]{64}", checksum)
        or row["relative_path"] != f"hf/{checksum}.{extension}"
    ):
        raise ValueError("Invalid prepared image cache path")
    image = row["image"]
    if (
        image["path"] is not None
        or not isinstance(image["bytes"], bytes)
        or hashlib.sha256(image["bytes"]).hexdigest() != checksum
    ):
        raise ValueError("Dataset image checksum mismatch")
    if len(image["bytes"]) > settings.max_image_bytes:
        raise ValueError("Dataset image exceeds MAX_IMAGE_BYTES")
    destination = safe_path(settings, row["relative_path"], must_exist=False)
    if destination.exists():
        if digest(destination) != checksum:
            raise ValueError("Cached image differs from the pinned dataset")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(image["bytes"])
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def select_images(settings, limit=50, scan_limit=1000, seed=42):
    """Select already-prepared image rows; source interpretation lives upstream."""
    if limit < 1 or scan_limit < 1:
        raise ValueError("Selection limits must be positive")
    rows, release = load_catalogue(settings)
    candidates, seen, skipped, scanned = {}, set(), Counter(), 0
    for row in islice(rows, scan_limit):
        scanned += 1
        if row.get("schema_version") != CATALOGUE_VERSION:
            raise ValueError("Unsupported prepared catalogue schema")
        if row["image_id"] in seen:
            raise ValueError("Duplicate image identity in catalogue")
        seen.add(row["image_id"])
        if (
            row["mime_type"] not in ("image/jpeg", "image/png")
            or row["width"] * row["height"] > settings.max_image_pixels
            or len(row["image"]["bytes"]) > settings.max_image_bytes
        ):
            skipped["app_image_limits"] += 1
            continue
        priority = hashlib.sha256(f"{seed}:{row['location']}".encode()).hexdigest()
        if len(candidates) >= limit:
            worst = max(candidates)
            if priority >= worst:
                continue
            del candidates[worst]
        candidates[priority] = row
    selected = []
    for _, row in sorted(candidates.items()):
        cache_image(settings, row)
        selected.append(
            {
                key: value
                for key, value in row.items()
                if key not in ("image", "schema_version")
            }
        )
    return selected, {
        "scanned": scanned,
        "selected": len(selected),
        "skipped": dict(skipped),
        "seed": seed,
        "dataset": release,
    }
