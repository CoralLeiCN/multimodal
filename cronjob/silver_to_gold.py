#!/usr/bin/env python3
"""Convert silver CSV rows with local images to a gold Parquet file."""

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

if __package__:
    from .match_images import index_images, match_location
else:
    from match_images import index_images, match_location

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = "smg_object_records_with_CC_images_09_04_2025.csv"


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def convert_csv_to_parquet(source, destination, *, images_dir=None):
    """Keep rows with available images and publish the checked Parquet atomically."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("Input and output must be different files")
    if destination.suffix.lower() != ".parquet":
        raise ValueError("Output filename must end with .parquet")
    source_hash = file_sha256(source)
    # Read the header as data so pandas cannot rename or infer an index from it.
    # The Python parser keeps absent fields distinct from explicit empty cells.
    frame = pd.read_csv(
        source, header=None, engine="python", encoding="utf-8-sig",
        dtype=pd.StringDtype(storage="pyarrow"), na_filter=False,
    )
    if frame.isna().any().any():
        raise ValueError("Every CSV row must have the same number of fields as the header")
    columns = frame.iloc[0].tolist()
    if not columns or any(not column.strip() for column in columns):
        raise ValueError("The silver CSV must have a header with nonempty column names")
    if len(set(columns)) != len(columns):
        raise ValueError("The silver CSV has duplicate column names")

    frame = frame.iloc[1:].reset_index(drop=True)
    frame.columns = columns
    if "image" not in columns:
        raise ValueError("The silver CSV must have an image column")
    images_dir = Path(images_dir or PROJECT_ROOT / "data/images").resolve()
    if not images_dir.is_dir():
        raise ValueError(f"Image directory does not exist: {images_dir}")
    image_index = index_images(images_dir)
    available_images = {
        location for location in frame["image"].unique()
        if match_location(location, image_index)[2]
    }
    source_rows = len(frame)
    frame = frame.loc[frame["image"].isin(available_images)].reset_index(drop=True)
    dropped_rows = source_rows - len(frame)
    frame.attrs = {
        "layer": "gold",
        "source_file": str(source),
        "source_sha256": source_hash,
        "images_dir": str(images_dir),
        "source_rows": source_rows,
        "dropped_rows": dropped_rows,
        "conversion": "Only rows with local images; all columns retained as strings, including empty values",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.stem}-", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        frame.to_parquet(temporary_path, engine="pyarrow", compression="zstd", index=False)
        result = pd.read_parquet(temporary_path, engine="pyarrow")
        if not result.equals(frame) or result.attrs != frame.attrs:
            raise ValueError("Written Parquet values, schema, or metadata do not match the filtered CSV")
        if file_sha256(source) != source_hash:
            raise ValueError("The silver CSV changed during conversion; run the conversion again")
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return {
        "source": str(source),
        "destination": str(destination),
        "rows": len(frame),
        "source_rows": source_rows,
        "dropped_rows": dropped_rows,
        "columns": columns,
        "source_sha256": source_hash,
        "source_bytes": source.stat().st_size,
        "parquet_bytes": destination.stat().st_size,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=PROJECT_ROOT / "data/silver" / DEFAULT_CSV,
        help="Official silver CSV; defaults to the downloaded SMG object export",
    )
    parser.add_argument(
        "--output", type=Path,
        help="Gold Parquet path; defaults to data/gold/object_records.parquet",
    )
    parser.add_argument(
        "--images-dir", type=Path, default=PROJECT_ROOT / "data/images",
        help="Local image directory, searched recursively; defaults to data/images",
    )
    args = parser.parse_args(argv)
    destination = args.output or PROJECT_ROOT / "data/gold/object_records.parquet"
    try:
        result = convert_csv_to_parquet(args.input, destination, images_dir=args.images_dir)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print(f"Silver: {result['source']}")
    print(f"Gold: {result['destination']}")
    print(f"Converted {result['rows']:,} rows and {len(result['columns'])} columns")
    print(f"Dropped {result['dropped_rows']:,} of {result['source_rows']:,} source rows without an available image")
    print(f"Size: {result['source_bytes']:,} CSV bytes -> {result['parquet_bytes']:,} Parquet bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
