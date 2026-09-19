"""Exercise the restored migration without a shared PostgreSQL database."""

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from app.core.config import ROOT
from sqlalchemy import create_engine, inspect, text


def test_revision_0004_preserves_existing_images_and_storage_urls(tmp_path):
    config = Config()
    config.set_main_option("script_location", str(ROOT / "backend/app/alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_current_head() == "0004"
    assert list(scripts.iterate_revisions("head", "0004")) == []
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE images (image_id VARCHAR PRIMARY KEY, title VARCHAR)"
                )
            )
            connection.execute(
                text("INSERT INTO images VALUES ('image-1', 'Existing image')")
            )
            with Operations.context(MigrationContext.configure(connection)):
                scripts.get_revision("0004").module.upgrade()
            assert connection.execute(
                text("SELECT title, r2_url FROM images")
            ).one() == ("Existing image", None)
            column = next(
                c
                for c in inspect(connection).get_columns("images")
                if c["name"] == "r2_url"
            )
            assert column["nullable"]
            connection.execute(
                text("UPDATE images SET r2_url = 'https://example.com/image.jpg'")
            )
            # An already stamped 0004 database runs no upgrade operations.
            for revision in reversed(list(scripts.iterate_revisions("head", "0004"))):
                with Operations.context(MigrationContext.configure(connection)):
                    revision.module.upgrade()
            assert (
                connection.scalar(text("SELECT r2_url FROM images"))
                == "https://example.com/image.jpg"
            )
    finally:
        engine.dispose()
