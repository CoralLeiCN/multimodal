"""Export a SQLite backup for sharing, keeping cached embeddings local."""

import argparse
import gzip
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export_catalogue(source: Path, output: Path) -> None:
    source, output = source.resolve(), output.resolve()
    if source == output:
        raise ValueError(
            "The snapshot must use a different path from the local database."
        )
    # Read-only mode also prevents a missing source from becoming an empty database.
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output.parent) as folder:
            backup_path = Path(folder) / "catalog.sqlite3"
            with closing(sqlite3.connect(backup_path)) as backup:
                original.backup(backup)
                backup.execute("DELETE FROM embedding_cache")
                backup.commit()
                # Rebuild the file so deleted vectors cannot remain in unused pages.
                backup.execute("VACUUM")
                backup.execute("PRAGMA journal_mode=DELETE")
                if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("The catalogue backup failed its integrity check.")
                if backup.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("The catalogue backup contains broken references.")
            compressed_path = Path(folder) / "catalog.sqlite3.gz"
            with (
                backup_path.open("rb") as contents,
                compressed_path.open("wb") as raw,
                gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as archive,
            ):
                shutil.copyfileobj(contents, archive)
            compressed_path.replace(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=ROOT / "data/search/catalog.sqlite3"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "catalogue/catalog.sqlite3.gz"
    )
    args = parser.parse_args()
    try:
        export_catalogue(args.source, args.output)
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f"Catalogue export failed: {error}\n")
    print(f"Exported catalogue without cached embeddings: {args.output}")


if __name__ == "__main__":
    main()
