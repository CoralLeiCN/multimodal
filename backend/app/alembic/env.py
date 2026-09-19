from alembic import context
from app import models  # noqa: F401
from app.core.config import Settings
from sqlalchemy import create_engine
from sqlmodel import SQLModel

config = context.config
url = config.get_main_option("sqlalchemy.url") or Settings().database_url
Settings().data_dir.mkdir(parents=True, exist_ok=True)
engine = create_engine(url)
with engine.connect() as connection:
    context.configure(
        connection=connection, target_metadata=SQLModel.metadata, render_as_batch=True
    )
    with context.begin_transaction():
        context.run_migrations()
