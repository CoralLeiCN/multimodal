from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, insert, select, text
from sqlalchemy.orm import Session

from app.agent_models import Workspace
from app.core.config import ROOT


def engine_for(settings):
    url = settings.database_url
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite:"):
        path = Path(url.removeprefix("sqlite:///"))
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path}"
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def setup(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")

    return engine


def migrate(engine, workspace):
    config = Config()
    config.set_main_option("script_location", str(ROOT / "backend/app/agent_alembic"))
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(731904281)"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        if (
            connection.execute(
                select(Workspace.id).where(Workspace.id == workspace)
            ).first()
            is None
        ):
            connection.execute(insert(Workspace).values(id=workspace))


@contextmanager
def transaction(engine):
    with Session(engine, expire_on_commit=False) as session:
        if engine.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
