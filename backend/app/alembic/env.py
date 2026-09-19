from alembic import context
from app import models  # noqa: F401
from app.core.config import Settings
from sqlalchemy import create_engine
from sqlmodel import SQLModel

config = context.config
url = config.get_main_option("sqlalchemy.url") or Settings().migration_database_url
engine = create_engine(url, hide_parameters=True)


def run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=SQLModel.metadata,
    )
    with context.begin_transaction():
        context.run_migrations()


try:
    if engine.dialect.name != "postgresql":
        raise ValueError("Catalogue migrations require PostgreSQL.")
    with engine.begin() as connection:
        # Serialize startup migrations from collaborating app processes.
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(734682109)")
        run_migrations(connection)
finally:
    engine.dispose()
