"""Retire provider URLs while preserving catalogue identity and metadata."""

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from app.core.config import ROOT
from sqlalchemy import create_engine, inspect, text


def test_storage_migration_preserves_images_and_keeps_upgrade_history(tmp_path):
    config = Config()
    config.set_main_option("script_location", str(ROOT / "backend/app/alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_current_head() == "0006"
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE images (image_id VARCHAR PRIMARY KEY, relative_path VARCHAR, title VARCHAR)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO images VALUES ('image-1', 'folder/photo.jpg', 'Existing image')"
                )
            )
            for table in ("index_generations", "image_associations"):
                connection.execute(
                    text(f"CREATE TABLE {table} (id VARCHAR PRIMARY KEY)")
                )
                connection.execute(text(f"INSERT INTO {table} VALUES ('existing')"))
            with Operations.context(MigrationContext.configure(connection)):
                scripts.get_revision("0004").module.upgrade()
            connection.execute(
                text("UPDATE images SET r2_url = 'https://retired.example/image.jpg'")
            )
            for revision in reversed(list(scripts.iterate_revisions("head", "0004"))):
                with Operations.context(MigrationContext.configure(connection)):
                    revision.module.upgrade()
            assert "r2_url" not in {
                c["name"] for c in inspect(connection).get_columns("images")
            }
            assert connection.execute(text("SELECT * FROM images")).one() == (
                "image-1",
                "folder/photo.jpg",
                "Existing image",
            )
            assert connection.execute(
                text("SELECT * FROM index_generations")
            ).one() == ("existing", "", "", "")
            assert (
                connection.scalar(text("SELECT record_type FROM image_associations"))
                == ""
            )
            with Operations.context(MigrationContext.configure(connection)):
                scripts.get_revision("0006").module.downgrade()
                scripts.get_revision("0005").module.downgrade()
            assert [
                c["name"] for c in inspect(connection).get_columns("index_generations")
            ] == ["id"]
            assert connection.scalar(text("SELECT r2_url FROM images")) is None
    finally:
        engine.dispose()
