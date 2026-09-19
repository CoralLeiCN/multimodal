"""Verify Qdrant and copy a schema-0003 SQLite catalogue to empty PostgreSQL."""

import argparse
import json
from pathlib import Path

from app.core.config import ROOT, Settings
from app.core.db import make_engine, migrate
from app.models import Generation, Image, ServiceState
from app.services.catalogue_transfer import transfer_catalogue
from app.services.qdrant_store import VectorStore
from sqlalchemy import create_engine
from sqlmodel import Session, select


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--env-file", type=Path, help="Optional destination dotenv file"
    )
    args = parser.parse_args()
    settings = (
        Settings(_env_file=(ROOT / ".env", args.env_file))
        if args.env_file
        else Settings()
    )
    if not settings.catalogue_database_url:
        parser.error("Set DATABASE_URL to the destination PostgreSQL database.")
    path = args.source.resolve(strict=True)
    source = create_engine(f"sqlite:///file:{path.as_posix()}?mode=ro&uri=true")
    target = make_engine(settings)
    vectors = VectorStore(settings)
    try:
        with source.connect() as connection:
            # Explicit BEGIN gives all reads the same snapshot, including WAL data.
            connection.exec_driver_sql("BEGIN")
            if connection.exec_driver_sql("PRAGMA integrity_check").scalar() != "ok":
                raise ValueError("Source catalogue failed its integrity check.")
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise ValueError("Source catalogue has invalid foreign keys.")
            version = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar()
            if version != "0003":
                raise ValueError(
                    "Source must be a legacy schema-0003 catalogue; current migrations require PostgreSQL."
                )
            with Session(bind=connection) as session:
                state = session.get(ServiceState, 1)
                generation = (
                    session.get(Generation, state.active_generation) if state else None
                )
                if generation is None or generation.status != "ready":
                    raise ValueError("The source must have a ready active generation.")
                if generation.config_hash != settings.config_hash:
                    raise ValueError(
                        "Embedding settings do not match the source catalogue."
                    )
                images = session.exec(
                    select(Image).where(Image.generation_id == generation.id)
                ).all()
                if generation.count != len(images):
                    raise ValueError(
                        "Source generation count does not match its image rows."
                    )
                vectors.verify(generation, images)
            migrate(settings)
            counts = transfer_catalogue(connection, target)
        print(json.dumps({"status": "imported", "tables": counts}, indent=2))
    finally:
        vectors.close()
        target.dispose()
        source.dispose()


if __name__ == "__main__":
    main()
