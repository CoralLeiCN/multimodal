#!/usr/bin/env python3
"""Index local images into Qdrant and save their configured R2 URLs in PostgreSQL."""

import argparse
import json
import sys
from pathlib import Path

import logfire
from app.core.config import ROOT, Settings
from app.core.db import make_engine, migrate
from app.core.locking import ingestion_lock
from app.core.telemetry import configure_telemetry
from app.services.embeddings import GeminiEmbeddings, SearchError
from app.services.ingestion import create_generation, run_ingestion, saved_generation_id
from app.services.qdrant_store import VectorStore
from app.services.selection import select_images


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        help="Select up to this many images (positive integer); omit to reuse the saved selection",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        help="Metadata records to scan (positive integer); default 1000",
    )
    parser.add_argument("--seed", type=int, help="Repeatable sample seed; default 42")
    parser.add_argument("--metadata", type=Path, action="append")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Save catalogue rows without calling Gemini, Qdrant, or R2",
    )
    parser.add_argument("--resume")
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Concurrent image batches, 1–10; default 10",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Maximum images per embedding request, 1–100; default 10",
    )
    args = parser.parse_args(argv)
    if args.resume and (
        args.dry_run
        or args.prepare_only
        or args.metadata
        or any(value is not None for value in (args.limit, args.scan_limit, args.seed))
    ):
        parser.error("resume cannot be combined with selection options")
    if (
        args.limit is None
        and not args.resume
        and (
            args.dry_run
            or args.prepare_only
            or args.metadata
            or args.scan_limit is not None
            or args.seed is not None
        )
    ):
        parser.error(
            "selection options require --limit; omit them to reuse the saved selection"
        )
    args.scan_limit = args.scan_limit if args.scan_limit is not None else 1000
    args.seed = args.seed if args.seed is not None else 42
    if (args.limit is not None and args.limit < 1) or args.scan_limit < 1:
        parser.error("limit and scan-limit must be positive integers")
    if not 1 <= args.workers <= 10:
        parser.error("workers must be 1–10")
    if not 1 <= args.batch_size <= 100:
        parser.error("batch-size must be 1–100")
    settings = Settings()
    configure_telemetry(settings, "multimodal-indexer", indexing=True)
    embeddings = GeminiEmbeddings(settings)
    vectors = None
    generation_id = args.resume
    try:
        with ingestion_lock(settings, shared=not args.dry_run):
            if args.limit is not None:
                metadata = args.metadata or sorted(
                    (ROOT / "data/bronze").glob("smg_object_records*.json")
                )
                selected, report = select_images(
                    settings, metadata, args.limit, args.scan_limit, args.seed
                )
                print(json.dumps(report), flush=True)
                if not selected:
                    raise ValueError("No images were selected.")
                if args.dry_run:
                    print(
                        json.dumps(
                            {"titles": [item["title"] for item in selected]},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    return 0
            engine = make_engine(settings)
            migrate(settings)
            if args.limit is not None:
                generation_id = create_generation(engine, settings, selected, report)
            elif generation_id is None:
                generation_id = saved_generation_id(engine, settings)
                print(
                    "Reusing saved selection; skipping metadata sampling.", flush=True
                )
            print(f"Run ID: {generation_id}", flush=True)
            if args.prepare_only:
                print(
                    f"Selection saved to {settings.data_dir / 'selections' / (generation_id + '.jsonl')}",
                    flush=True,
                )
                return 0
            vectors = VectorStore(settings)
            result = run_ingestion(
                engine,
                settings,
                generation_id,
                embeddings,
                vectors,
                progress=lambda message: print(message, flush=True),
                workers=args.workers,
                batch_size=args.batch_size,
            )
            print(json.dumps(result), flush=True)
            return 0
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 -- sanitize errors at the CLI boundary
        message = (
            str(error)
            if isinstance(error, (ValueError, SearchError))
            else type(error).__name__
        )
        print(f"Ingestion stopped: {message}", file=sys.stderr)
        if generation_id:
            print(f"Resume with --resume {generation_id}", file=sys.stderr)
        return 2
    finally:
        embeddings.close()
        if vectors:
            vectors.close()
        logfire.force_flush(timeout_millis=2000)


if __name__ == "__main__":
    raise SystemExit(main())
