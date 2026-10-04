"""One set of catalogue queries for native PostgreSQL and Neon HTTPS reads."""

from typing import Protocol

from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.models import Association, Generation, Image, ServiceState
from app.services.embeddings import SearchError
from app.services.metadata import catalogue_filter


def unavailable():
    return SearchError(
        "The collection catalogue is unavailable. Check its connection and migrations.",
        "catalogue_unavailable",
    )


class Queries(Protocol):
    def read(self, *statements: Select) -> list[list[dict]]: ...


class PostgresQueries:
    def __init__(self, engine):
        self.engine = engine

    def read(self, *statements):
        try:
            with self.engine.connect() as connection:
                return [
                    [dict(row) for row in connection.execute(statement).mappings()]
                    for statement in statements
                ]
        except SQLAlchemyError:
            raise unavailable() from None


class Catalogue:
    def __init__(self, queries: Queries):
        self.queries = queries

    def generation(self):
        (rows,) = self.queries.read(
            select(Generation)
            .join(ServiceState, ServiceState.active_generation == Generation.id)
            .where(ServiceState.id == 1)
        )
        return Generation.model_validate(rows[0]) if rows else None

    def images(self, generation_id, ids=None):
        statement = select(Image).where(Image.generation_id == generation_id)
        if ids is not None:
            statement = statement.where(Image.image_id.in_(ids))
        (rows,) = self.queries.read(statement)
        return [Image.model_validate(row) for row in rows]

    def associations(self, generation_id, ids=None):
        statement = select(Association).where(
            Association.generation_id == generation_id
        )
        if ids is not None:
            statement = statement.where(Association.image_id.in_(ids))
        (rows,) = self.queries.read(statement.order_by(Association.id))
        return [Association.model_validate(row) for row in rows]

    def browse(self, generation_id, filters, last_id, limit):
        predicate = catalogue_filter(filters)
        counts, rows = self.queries.read(
            select(func.count().label("count"))
            .select_from(Image)
            .where(Image.generation_id == generation_id, predicate),
            select(Image.image_id)
            .where(
                Image.generation_id == generation_id,
                Image.image_id > last_id,
                predicate,
            )
            .order_by(Image.image_id)
            .limit(limit + 1),
        )
        return counts[0]["count"], [row["image_id"] for row in rows]

    def record_images(self, generation_id, record_uid):
        (rows,) = self.queries.read(
            select(Association.image_id)
            .where(
                Association.generation_id == generation_id,
                Association.record_uid == record_uid,
            )
            .distinct()
            .order_by(Association.image_id)
        )
        return [row["image_id"] for row in rows]
