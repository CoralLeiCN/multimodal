from alembic import command
from alembic.config import Config
from sqlmodel import create_engine

from app.core.config import ROOT, Settings
from app.models import EmbeddingCache


def make_engine(settings: Settings):
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 15},
        hide_parameters=True,
    )


def make_cache_engine(settings: Settings):
    path = settings.embedding_cache_path
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30}
    )
    try:
        EmbeddingCache.metadata.create_all(engine)
    except BaseException:
        engine.dispose()
        raise
    return engine


def migrate(settings: Settings):
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/app/alembic"))
    config.set_main_option(
        "sqlalchemy.url", settings.migration_database_url.replace("%", "%%")
    )
    command.upgrade(config, "head")
