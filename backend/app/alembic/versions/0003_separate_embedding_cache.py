"""Move cached embeddings to their own local SQLite database."""

from pathlib import Path

import sqlalchemy as sa
from alembic import op
from app.core.config import Settings
from app.core.db import make_cache_engine
from app.models import EmbeddingCache
from sqlalchemy.dialects.sqlite import insert

revision = "0003"
down_revision = "0002"


def cache_engine():
    # Use the actual migration connection, including explicit Alembic URL overrides.
    path = Path(op.get_bind().engine.url.database).resolve()
    return make_cache_engine(Settings(_env_file=None, sqlite_path=path))


def upgrade():
    catalogue = op.get_bind()
    if sa.inspect(catalogue).has_table("embedding_cache"):
        engine = cache_engine()
        try:
            # Commit the cache copy before removing any source rows. Retrying after
            # an interruption preserves entries already copied into the cache.
            with engine.begin() as cache:
                rows = catalogue.execute(sa.select(EmbeddingCache.__table__)).mappings()
                for batch in rows.partitions(100):
                    cache.execute(
                        insert(EmbeddingCache).on_conflict_do_nothing(
                            index_elements=["key"]
                        ),
                        [dict(row) for row in batch],
                    )
            op.drop_table("embedding_cache")
        finally:
            engine.dispose()
    # One-time compaction also removes old vector data from free pages. If the
    # process stopped after DROP, rerunning this revision still completes VACUUM.
    with op.get_context().autocommit_block():
        catalogue.exec_driver_sql("VACUUM")


def downgrade():
    catalogue = op.get_bind()
    EmbeddingCache.__table__.create(catalogue)
    engine = cache_engine()
    try:
        with engine.connect() as cache:
            rows = cache.execute(sa.select(EmbeddingCache.__table__)).mappings()
            for batch in rows.partitions(100):
                catalogue.execute(
                    sa.insert(EmbeddingCache), [dict(row) for row in batch]
                )
    finally:
        engine.dispose()
