import hashlib
import importlib.util
import io
import warnings
from collections import Counter
from itertools import islice
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import NAMESPACE_URL, uuid5

from PIL import Image as PillowImage

from app.core.config import ROOT, Settings
from app.services.embeddings import SearchError
from app.services.metadata import extract_metadata, filter_rows

# Share the existing, tested streaming parser without installing the data scripts.
_spec = importlib.util.spec_from_file_location(
    "image_source", ROOT / "cronjob/match_images.py"
)
source = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(source)

KNOWN_LICENCES = {
    "CC BY-NC-SA 4.0",
    "CC-BY-NC-SA 4.0",
    "CC BY-NC-ND 4.0",
    "Open Government Licence v3.0",
    "CC BY 4.0",
    "CC0",
}


def validate_image(data: bytes, settings: Settings) -> tuple[str, int, int]:
    if len(data) > settings.max_image_bytes:
        raise SearchError(
            "Choose an image smaller than 10 MiB.", "image_too_large", 413
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", PillowImage.DecompressionBombWarning)
            with PillowImage.open(io.BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG"}:
                    raise SearchError(
                        "Choose a JPEG or PNG image.", "unsupported_image", 415
                    )
                if image.width * image.height > settings.max_image_pixels:
                    raise SearchError(
                        "Choose an image under 20 million pixels.",
                        "image_too_large",
                        413,
                    )
                image.load()
                return (
                    "image/jpeg" if image.format == "JPEG" else "image/png",
                    image.width,
                    image.height,
                )
    except SearchError:
        raise
    except (PillowImage.DecompressionBombWarning, PillowImage.DecompressionBombError):
        raise SearchError(
            "The image dimensions exceed the supported limit.", "image_too_large", 413
        ) from None
    except (OSError, ValueError, SyntaxError):
        raise SearchError(
            "This file could not be decoded as an image.", "invalid_image", 422
        ) from None


def safe_path(settings: Settings, relative: str) -> Path:
    root = settings.absolute(settings.image_root)
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise SearchError("This collection image is unavailable.", "image_missing", 404)
    return path


def select_images(
    settings: Settings,
    metadata: list[Path],
    limit: int = 50,
    scan_limit: int = 1000,
    seed: int = 42,
):
    """Keep at most limit metadata candidates using stable hash sampling."""
    root = settings.absolute(settings.image_root)
    roots = [root, *sorted(path for path in root.iterdir() if path.is_dir())]
    candidates = {}
    skipped = Counter()
    scanned = 0
    for metadata_path in metadata:
        remaining = scan_limit - scanned
        if remaining <= 0:
            break
        for record in islice(source.iter_records(metadata_path), remaining):
            scanned += 1
            for media in record.get("multimedia") or []:
                rights = (media.get("legal") or {}).get("rights") or []
                licences = [right.get("licence", "") for right in rights]
                if not licences or any(
                    licence not in KNOWN_LICENCES for licence in licences
                ):
                    skipped["unresolved_rights"] += 1
                    continue
                location = (
                    (media.get("@processed") or {}).get("medium_thumbnail") or {}
                ).get("location")
                if not isinstance(location, str) or not location:
                    skipped["missing_location"] += 1
                    continue
                try:
                    location = unquote(urlsplit(location).path).lstrip("/")
                except ValueError:
                    skipped["invalid_location"] += 1
                    continue
                if (
                    not location
                    or ".." in PurePosixPath(location).parts
                    or "\\" in location
                ):
                    skipped["invalid_location"] += 1
                    continue
                matches = {
                    path.resolve()
                    for base in roots
                    if (path := base / location).is_file()
                    and path.resolve().is_relative_to(root)
                }
                if len(matches) != 1:
                    skipped["missing" if not matches else "ambiguous"] += 1
                    continue
                priority = hashlib.sha256(f"{seed}:{location}".encode()).hexdigest()
                if location not in candidates and len(candidates) >= limit:
                    worst = max(candidates, key=lambda key: candidates[key]["priority"])
                    if priority >= candidates[worst]["priority"]:
                        continue
                    del candidates[worst]
                metadata_fields = source.record_metadata(record)
                title = source.primary_value(record.get("title")) or record.get(
                    "summary", {}
                ).get("title", "")
                association = {
                    "record_uid": record.get("@admin", {}).get("uid", ""),
                    "image_uid": media.get("@admin", {}).get("uid", ""),
                    "source_json": metadata_path.name,
                    "title": title,
                    "description": source.primary_value(record.get("description")),
                    "date": metadata_fields["date"],
                    **extract_metadata(record),
                    "maker": metadata_fields["maker"],
                    "catalogue_identifiers": metadata_fields["catalogue_identifiers"],
                    "licence": source.joined_values(rights, "licence"),
                    "copyright": source.joined_values(rights, "copyright"),
                    "credit": (media.get("credit") or {}).get("value", ""),
                }
                candidate = candidates.setdefault(
                    location,
                    {
                        "image_id": str(
                            uuid5(NAMESPACE_URL, f"smg-collection-image:{location}")
                        ),
                        "location": location,
                        "relative_path": next(iter(matches))
                        .relative_to(root)
                        .as_posix(),
                        "priority": priority,
                        "title": title or "Untitled image",
                        "associations": [],
                    },
                )
                if association not in candidate["associations"]:
                    candidate["associations"].append(association)
    selected = []
    for candidate in sorted(candidates.values(), key=lambda item: item["priority"]):
        try:
            path = safe_path(settings, candidate["relative_path"])
            with path.open("rb") as stream:
                data = stream.read(settings.max_image_bytes + 1)
            mime, width, height = validate_image(data, settings)
        except (SearchError, OSError):
            skipped["invalid_file"] += 1
            continue
        candidate.update(
            filter_metadata=filter_rows(candidate["associations"]),
            checksum=hashlib.sha256(data).hexdigest(),
            mime_type=mime,
            width=width,
            height=height,
        )
        selected.append(candidate)
    return selected, {
        "scanned": scanned,
        "selected": len(selected),
        "skipped": dict(skipped),
        "seed": seed,
    }
