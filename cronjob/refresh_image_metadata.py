#!/usr/bin/env python3
"""Set up date/place/category filters for an existing sample without embeddings."""

import argparse
import fcntl
import json
import sys
from pathlib import Path

from app.core.config import ROOT, Settings
from app.core.db import make_engine, migrate
from app.services.embeddings import SearchError
from app.services.metadata_refresh import refresh_metadata
from app.services.qdrant_store import VectorStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--metadata", type=Path, action="append")
    parser.add_argument("--scan-limit", type=int, default=1000)
    args = parser.parse_args(argv)
    if not 1 <= args.scan_limit <= 10000:
        parser.error("scan-limit must be 1–10000")
    settings = Settings()
    engine = vectors = None
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with (settings.data_dir / ".ingestion.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(
                    "Another indexing command is already running."
                ) from None
            migrate(settings)
            engine = make_engine(settings)
            vectors = VectorStore(settings)
            metadata = args.metadata or sorted(
                (ROOT / "data/bronze").glob("smg_object_records*.json")
            )
            print(
                json.dumps(
                    refresh_metadata(
                        engine,
                        settings,
                        args.generation,
                        metadata,
                        args.scan_limit,
                        vectors,
                    )
                )
            )
        return 0
    except Exception as error:  # noqa: BLE001 -- sanitize transport failures at the CLI boundary
        message = (
            str(error)
            if isinstance(error, (ValueError, SearchError))
            else type(error).__name__
        )
        print(f"Metadata refresh stopped: {message}", file=sys.stderr)
        return 2
    finally:
        if vectors:
            vectors.close()
        if engine:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
