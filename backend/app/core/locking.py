"""Coordinate catalogue writers locally and across Neon collaborators."""

import fcntl
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


@contextmanager
def ingestion_lock(settings, *, shared=True):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with (settings.data_dir / ".ingestion.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another indexing command is already running.") from None
        if not shared:
            yield
            return
        # A dedicated direct session owns the lock throughout external API calls.
        engine = create_engine(
            settings.migration_database_url,
            poolclass=NullPool,
            connect_args={"connect_timeout": 15},
            hide_parameters=True,
        )
        try:
            with engine.connect() as connection:
                acquired = connection.exec_driver_sql(
                    "SELECT pg_try_advisory_lock(734682110)"
                ).scalar()
                connection.commit()
                if not acquired:
                    raise ValueError("Another collaborator is updating this catalogue.")
                try:
                    yield
                finally:
                    # NullPool closes the session and releases its advisory lock.
                    connection.close()
        finally:
            engine.dispose()
