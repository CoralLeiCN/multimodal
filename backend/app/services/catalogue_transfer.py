"""Copy a catalogue into an empty database without changing its index identity."""

import hashlib
import json

from sqlalchemy import func, inspect, literal, select

from app.models import Association, Generation, Image, Ingestion, ServiceState

TABLES = tuple(
    model.__table__
    for model in (Generation, Image, Association, Ingestion, ServiceState)
)


def catalogue_rows(connection, table):
    columns = list(table.columns)
    if table.name == "images" and "r2_url" not in {
        column["name"] for column in inspect(connection).get_columns("images")
    }:
        # Historical schema-0003 SQLite catalogues predate R2 references.
        columns = [
            literal(None).label("r2_url") if column.name == "r2_url" else column
            for column in columns
        ]
    return connection.execute(select(*columns).order_by(*table.primary_key)).mappings()


def fingerprint(connection, table):
    digest = hashlib.sha256()
    count = 0
    rows = catalogue_rows(connection, table)
    for row in rows:
        digest.update(
            json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode()
        )
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


def transfer_catalogue(source, destination):
    """Caller owns the stable source transaction; destination commits atomically."""
    counts = {}
    with destination.begin() as target:
        if target.dialect.name == "postgresql":
            target.exec_driver_sql("SET LOCAL lock_timeout = '15s'")
            names = ", ".join(table.name for table in TABLES)
            target.exec_driver_sql(f"LOCK TABLE {names} IN SHARE ROW EXCLUSIVE MODE")
        if any(
            target.scalar(select(func.count()).select_from(table)) for table in TABLES
        ):
            raise ValueError(
                "Destination catalogue is not empty; refusing to overwrite it."
            )
        for table in TABLES:
            rows = catalogue_rows(source, table)
            for batch in rows.partitions(500):
                target.execute(table.insert(), [dict(row) for row in batch])
            expected = fingerprint(source, table)
            if fingerprint(target, table) != expected:
                raise ValueError(
                    f"Imported contents differ for {table.name}; rolling back."
                )
            counts[table.name] = expected[0]
    return counts
