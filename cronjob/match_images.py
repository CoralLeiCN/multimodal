#!/usr/bin/env python3
"""Build an image/metadata manifest from local SMG JSON exports."""

import argparse
import csv
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".gif"}
# Keep the same columns even when a field is empty throughout a dataset.
FIELDS = [
    "source_json", "record_uid", "image_uid", "title", "description",
    "image_location", "image_path", "status", "candidate_paths",
    "licence", "copyright", "credit",
    "date", "maker", "catalogue_identifiers", "measurements",
    "classification", "archival_level", "thumbnail_width", "thumbnail_height",
    "metadata_details_json",
]


def iter_records(path, chunk_size=1024 * 1024):
    """Read a top-level JSON array incrementally, retaining one chunk/record."""
    decoder = json.JSONDecoder()
    with Path(path).open(encoding="utf-8-sig") as stream:
        buffer, position, eof = "", 0, False
        state = "start"
        while True:
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position == len(buffer) and not eof:
                chunk = stream.read(chunk_size)
                buffer, position, eof = buffer[position:] + chunk, 0, not chunk
                continue
            if state == "end":
                if position < len(buffer):
                    raise ValueError(f"{path}: content after the JSON array")
                if eof:
                    return
            elif position == len(buffer):
                raise ValueError(f"{path}: unexpected end of JSON array")
            elif state == "start":
                if buffer[position] != "[":
                    raise ValueError(f"{path}: expected a top-level JSON array")
                position += 1
                state = "first"
            elif state in {"first", "record"}:
                if state == "first" and buffer[position] == "]":
                    position += 1
                    state = "end"
                    continue
                if buffer[position] != "{":
                    raise ValueError(f"{path}: expected a metadata object")
                try:
                    record, end = decoder.raw_decode(buffer, position)
                except json.JSONDecodeError:
                    if eof:
                        raise
                    chunk = stream.read(chunk_size)
                    buffer, position, eof = buffer[position:] + chunk, 0, not chunk
                    continue
                position, state = end, "separator"
                yield record
            else:
                char = buffer[position]
                if char not in {",", "]"}:
                    raise ValueError(f"{path}: expected ',' or ']' after a record")
                position += 1
                state = "record" if char == "," else "end"


def index_images(images_dir):
    """Use filenames to find candidates; matching still checks the full path."""
    index = defaultdict(list)

    def report_error(error):
        raise error

    for folder, directories, filenames in os.walk(images_dir, onerror=report_error):
        directories.sort()
        for filename in sorted(filenames):
            if Path(filename).suffix.lower() in IMAGE_EXTENSIONS:
                relative = (Path(folder) / filename).relative_to(images_dir)
                index[filename].append(relative.as_posix())
    return index


def match_location(location, index):
    if not location:
        return "no_thumbnail", "", []
    if not isinstance(location, str):
        return "invalid_location", "", []
    try:
        relative = unquote(urlsplit(location).path).lstrip("/")
    except ValueError:
        return "invalid_location", "", []
    parts = PurePosixPath(relative).parts
    if not parts or ".." in parts or "\\" in relative:
        return "invalid_location", "", []
    relative = PurePosixPath(relative).as_posix()
    candidates = [
        path for path in index.get(parts[-1], [])
        if path == relative or path.endswith("/" + relative)
    ]
    status = "matched" if len(candidates) == 1 else "ambiguous" if candidates else "missing"
    return status, relative, candidates


def primary_value(values):
    if not isinstance(values, list):
        return values if isinstance(values, str) else ""
    values = [item for item in values if isinstance(item, dict) and item.get("value")]
    chosen = next((item for item in values if item.get("primary")), values[0] if values else {})
    return chosen.get("value", "")


def joined_values(items, key):
    return "; ".join(dict.fromkeys(str(item[key]) for item in items if item.get(key)))


def join_text(values):
    return "; ".join(dict.fromkeys(str(value) for value in values if value is not None and value != ""))


def measurement_text(measurements):
    parts = [measurements["display"]] if measurements.get("display") else []
    for dimension in measurements.get("dimensions") or []:
        value = dimension.get("value")
        if value is None or value == "":
            continue
        text = f"{value} {dimension.get('units') or ''}".strip()
        if text in parts:
            continue
        label = dimension.get("dimension") or dimension.get("type")
        parts.append(f"{label}: {text}" if label else text)
    return join_text(parts)


def record_metadata(record):
    """Readable metadata plus original structures for the selected fields."""
    creation = record.get("creation") or {}
    dates = (creation.get("date") or []) + (record.get("date") or [])
    details = {
        key: record[key]
        for key in ("date", "identifier", "measurements", "category", "level")
        if key in record
    }
    creation_details = {key: creation[key] for key in ("date", "maker") if key in creation}
    if creation_details:
        details["creation"] = creation_details
    return {
        "date": join_text(item.get("value") for item in dates),
        "maker": join_text(
            (item.get("summary") or {}).get("title") or primary_value(item.get("name"))
            for item in creation.get("maker") or []
        ),
        "catalogue_identifiers": join_text(item.get("value") for item in record.get("identifier") or []),
        "measurements": measurement_text(record.get("measurements") or {}),
        "classification": join_text(item.get("name") or item.get("value") for item in record.get("category") or []),
        "archival_level": (record.get("level") or {}).get("value", ""),
        "metadata_details_json": json.dumps(details, ensure_ascii=False, separators=(",", ":")) if details else "",
    }


def thumbnail_dimensions(thumbnail):
    dimensions = (thumbnail.get("measurements") or {}).get("dimensions") or []
    pixels = {
        item.get("dimension"): item.get("value", "")
        for item in dimensions if item.get("units") == "pixels"
    }
    return {"thumbnail_width": pixels.get("width", ""), "thumbnail_height": pixels.get("height", "")}


def build_manifest(metadata_paths, images_dir, output_dir):
    print(f"Indexing images in {images_dir}", flush=True)
    index = index_images(images_dir)
    local_count = sum(len(paths) for paths in index.values())
    if not local_count:
        raise ValueError(f"No image files found in {images_dir}")
    print(f"Found {local_count:,} local image files", flush=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "image_manifest.csv"
    summary_path = output_dir / "image_match_summary.json"
    temporary_paths = []
    all_references, all_matched_locations, all_missing_locations = set(), set(), set()
    matched_files = set()
    dataset_summaries = []
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=output_dir,
            prefix=".image_manifest-", suffix=".tmp", delete=False,
        ) as output:
            temporary_paths.append(Path(output.name))
            writer = csv.DictWriter(output, fieldnames=FIELDS)
            writer.writeheader()
            for metadata_path in metadata_paths:
                counts = Counter({
                    key: 0 for key in [
                        "records", "records_with_matched_images", "records_without_matched_images",
                        "media_entries", "matched", "missing", "ambiguous",
                        "no_thumbnail", "invalid_location",
                    ]
                })
                references, matched_locations, missing_locations = set(), set(), set()
                for record in iter_records(metadata_path):
                    counts["records"] += 1
                    has_match = False
                    title = primary_value(record.get("title")) or record.get("summary", {}).get("title", "")
                    description = primary_value(record.get("description"))
                    metadata = record_metadata(record)
                    for media in record.get("multimedia") or []:
                        thumbnail = (media.get("@processed") or {}).get("medium_thumbnail") or {}
                        location = thumbnail.get("location")
                        status, relative, candidates = match_location(location, index)
                        counts["media_entries"] += 1
                        counts[status] += 1
                        if relative:
                            references.add(relative)
                        if status == "matched":
                            has_match = True
                            matched_locations.add(relative)
                            matched_files.add(candidates[0])
                        elif status == "missing":
                            missing_locations.add(relative)
                        rights = (media.get("legal") or {}).get("rights") or []
                        writer.writerow({
                            "source_json": str(metadata_path),
                            "record_uid": record.get("@admin", {}).get("uid", ""),
                            "image_uid": media.get("@admin", {}).get("uid", ""),
                            "title": title,
                            "description": description,
                            "image_location": location,
                            "image_path": str(images_dir / candidates[0]) if status == "matched" else "",
                            "status": status,
                            "candidate_paths": json.dumps([str(images_dir / p) for p in candidates]) if status == "ambiguous" else "",
                            "licence": joined_values(rights, "licence"),
                            "copyright": joined_values(rights, "copyright"),
                            "credit": (media.get("credit") or {}).get("value", ""),
                            **metadata,
                            **thumbnail_dimensions(thumbnail),
                        })
                    counts["records_with_matched_images" if has_match else "records_without_matched_images"] += 1
                    if counts["records"] % 50000 == 0:
                        print(f"  {metadata_path.name}: {counts['records']:,} records", flush=True)
                all_references.update(references)
                all_matched_locations.update(matched_locations)
                all_missing_locations.update(missing_locations)
                dataset_summaries.append({
                    "source_json": str(metadata_path), **counts,
                    "unique_referenced_images": len(references),
                    "unique_matched_images": len(matched_locations),
                    "unique_missing_images": len(missing_locations),
                })
                print(
                    f"{metadata_path.name}: {counts['records']:,} records; "
                    f"{counts['matched']:,} matched and {counts['missing']:,} missing image references",
                    flush=True,
                )
        totals = Counter()
        for dataset in dataset_summaries:
            totals.update({key: value for key, value in dataset.items() if key in counts})
        summary = {
            "generated_at": datetime.now(UTC).isoformat(),
            "images_dir": str(images_dir),
            "manifest": str(manifest_path),
            "local_image_files": local_count,
            "unique_referenced_images": len(all_references),
            "unique_matched_images": len(all_matched_locations),
            "unique_missing_images": len(all_missing_locations),
            "local_image_files_not_matched": local_count - len(matched_files),
            "totals": dict(totals),
            "datasets": dataset_summaries,
        }
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output_dir,
            prefix=".image_match_summary-", suffix=".tmp", delete=False,
        ) as output:
            temporary_paths.append(Path(output.name))
            json.dump(summary, output, ensure_ascii=False, indent=2)
            output.write("\n")
        # Publish only after every input file has been parsed successfully.
        os.replace(temporary_paths[0], manifest_path)
        os.replace(temporary_paths[1], summary_path)
        return summary
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, nargs="+", help="JSON exports; defaults to data/bronze/smg_*_records_with_CC_images_*.json")
    parser.add_argument("--images-dir", type=Path, default=PROJECT_ROOT / "data/images")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data/processed")
    parser.add_argument("--fail-on-missing", action="store_true", help="Exit 1 after writing outputs if any media entry cannot be matched")
    args = parser.parse_args()
    metadata_paths = args.metadata if args.metadata is not None else sorted((PROJECT_ROOT / "data/bronze").glob("smg_*_records_with_CC_images_*.json"))
    if not metadata_paths:
        parser.error("No metadata JSON files found; supply --metadata")
    if not args.images_dir.is_dir():
        parser.error(f"Image directory does not exist: {args.images_dir}")
    for path in metadata_paths:
        if not path.is_file():
            parser.error(f"Metadata file does not exist: {path}")
    try:
        summary = build_manifest(
            list(dict.fromkeys(path.resolve() for path in metadata_paths)),
            args.images_dir.resolve(), args.output_dir.resolve(),
        )
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print(f"Manifest: {summary['manifest']}")
    print(f"Summary: {args.output_dir.resolve() / 'image_match_summary.json'}")
    print(
        f"Unique images: {summary['unique_matched_images']:,} matched / "
        f"{summary['unique_referenced_images']:,} referenced; "
        f"{summary['unique_missing_images']:,} missing"
    )
    totals = summary["totals"]
    unmatched = sum(totals[status] for status in ("missing", "ambiguous", "no_thumbnail", "invalid_location"))
    return 1 if args.fail_on_missing and unmatched else 0


if __name__ == "__main__":
    sys.exit(main())
