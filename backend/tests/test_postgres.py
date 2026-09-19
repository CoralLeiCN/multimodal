"""PostgreSQL migrations and collaboration locking."""

import pytest
from app.core.db import migrate
from app.core.locking import ingestion_lock
from sqlalchemy import inspect, text


def test_migrations_are_repeatable_and_keep_cache_local(setup):
    settings, engine, *_ = setup
    migrate(settings)
    migrate(settings)
    assert not inspect(engine).has_table("embedding_cache")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            == "0004"
        )


def test_postgres_writer_lock_across_workspaces(setup, tmp_path):
    settings = setup[0]
    collaborator = settings.model_copy(update={"search_data_dir": tmp_path / "other"})
    with (
        ingestion_lock(settings),
        pytest.raises(ValueError, match="collaborator"),
        ingestion_lock(collaborator),
    ):
        pytest.fail("Concurrent writer entered the shared catalogue")
    with ingestion_lock(collaborator):
        pass
