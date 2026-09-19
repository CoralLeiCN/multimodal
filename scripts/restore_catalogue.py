"""Restore the shared SQLite snapshot without replacing an existing catalogue."""

import argparse
import gzip
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/search/catalog.sqlite3"
    )
    args = parser.parse_args()
    with gzip.open(ROOT / "catalogue/catalog.sqlite3.gz", "rb") as source:
        contents = source.read()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("xb") as target:
            target.write(contents)
    except FileExistsError:
        parser.exit(1, f"Catalogue already exists: {args.output}\n")
    print(f"Restored catalogue: {args.output}")


if __name__ == "__main__":
    main()
