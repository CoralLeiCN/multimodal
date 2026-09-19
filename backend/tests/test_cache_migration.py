import gzip
import sqlite3

import pytest
from alembic.config import Config
from app.core import db
from app.core.config import ROOT, Settings
from app.models import EmbeddingCache
from sqlalchemy import Engine, event, inspect
from sqlmodel import Session, select

from scripts.export_catalogue import export_catalogue


@pytest.fixture
def legacy_catalogue(tmp_path):
    settings = Settings(_env_file=None, sqlite_path=tmp_path / "catalog.sqlite3")
    # Revision 0002 is the schema used by existing catalogues.
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/app/alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    db.command.upgrade(config, "0002")
    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.executemany(
            "INSERT INTO embedding_cache VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "legacy-cache-marker",
                    "checksum",
                    "config",
                    "[1.0, 0.0]",
                    "2026-09-19",
                ),
                ("shared-key", "shared", "config", "[0.0, 1.0]", "2026-09-19"),
            ],
        )
    return settings


def assert_separated(settings):
    with sqlite3.connect(settings.sqlite_path) as catalogue:
        assert catalogue.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0003",)
        assert not catalogue.execute(
            "SELECT 1 FROM sqlite_master WHERE name='embedding_cache'"
        ).fetchone()
        assert catalogue.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert b"legacy-cache-marker" not in settings.sqlite_path.read_bytes()
    engine = db.make_cache_engine(settings)
    try:
        assert inspect(engine).get_table_names() == ["embedding_cache"]
        with Session(engine) as session:
            assert session.get(EmbeddingCache, "legacy-cache-marker").vector == [
                1.0,
                0.0,
            ]
            assert len(session.exec(select(EmbeddingCache)).all()) >= 2
    finally:
        engine.dispose()


def test_migration_moves_vectors_and_preserves_existing_local_cache(legacy_catalogue):
    settings = legacy_catalogue
    engine = db.make_cache_engine(settings)
    with Session(engine) as session:
        session.add(
            EmbeddingCache(
                key="shared-key",
                checksum="shared",
                config_hash="config",
                vector=[0.5, 0.5],
            )
        )
        session.add(
            EmbeddingCache(
                key="local-only",
                checksum="other",
                config_hash="config",
                vector=[1.0, 0.0],
            )
        )
        session.commit()
    engine.dispose()
    db.migrate(settings)
    db.migrate(settings)
    assert_separated(settings)
    engine = db.make_cache_engine(settings)
    try:
        with Session(engine) as session:
            assert session.get(EmbeddingCache, "shared-key").vector == [0.5, 0.5]
            assert session.get(EmbeddingCache, "local-only") is not None
    finally:
        engine.dispose()


@pytest.mark.parametrize("failure", ["cache_copy", "compaction"])
def test_migration_failure_can_resume_without_losing_cache(
    legacy_catalogue, monkeypatch, failure
):
    settings = legacy_catalogue

    def unavailable(*_args):
        raise OSError("cache unavailable")

    def interrupt_vacuum(_connection, _cursor, statement, _parameters, _context, _many):
        if statement == "VACUUM":
            raise OSError("compaction interrupted")

    with monkeypatch.context() as patch:
        if failure == "cache_copy":
            patch.setattr(db, "make_cache_engine", unavailable)
        else:
            event.listen(Engine, "before_cursor_execute", interrupt_vacuum)
        try:
            with pytest.raises(OSError):
                db.migrate(settings)
        finally:
            if failure == "compaction":
                event.remove(Engine, "before_cursor_execute", interrupt_vacuum)
    if failure == "cache_copy":
        with sqlite3.connect(settings.sqlite_path) as original:
            assert original.execute(
                "SELECT count(*) FROM embedding_cache"
            ).fetchone() == (2,)
    db.migrate(settings)
    assert_separated(settings)


def test_export_requires_migration_and_preserves_previous_snapshot(
    legacy_catalogue, tmp_path
):
    snapshot = tmp_path / "catalog.sqlite3.gz"
    snapshot.write_bytes(b"previous snapshot")
    with pytest.raises(ValueError, match="schema migrations"):
        export_catalogue(legacy_catalogue.sqlite_path, snapshot)
    assert snapshot.read_bytes() == b"previous snapshot"
    db.migrate(legacy_catalogue)
    export_catalogue(legacy_catalogue.sqlite_path, snapshot)
    assert b"legacy-cache-marker" not in gzip.decompress(snapshot.read_bytes())
    assert_separated(legacy_catalogue)


def test_cache_cannot_alias_catalogue(legacy_catalogue):
    settings = legacy_catalogue
    settings.embedding_cache_path.hardlink_to(settings.sqlite_path)
    with pytest.raises(ValueError, match="separate"):
        db.migrate(settings)
    with sqlite3.connect(settings.sqlite_path) as original:
        assert original.execute("SELECT count(*) FROM embedding_cache").fetchone() == (
            2,
        )
