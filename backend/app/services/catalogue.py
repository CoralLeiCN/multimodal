"""One set of catalogue queries for native PostgreSQL and Neon HTTPS reads."""

from typing import Protocol

from sqlalchemy import Integer, Select, cast, column, func, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError

from app.models import Association, Generation, Image, ServiceState
from app.services.embeddings import SearchError
from app.services.metadata import catalogue_filter, labels

# Each page is a separate HTTPS request, not another query in the same batch.
CATALOGUE_PAGE_SIZE = 500


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

    def _rows(self, statement, key):
        """Read a published generation in bounded pages using a unique, stable key."""
        after = None
        while True:
            page = statement.order_by(key).limit(CATALOGUE_PAGE_SIZE)
            if after is not None:
                page = page.where(key > after)
            (rows,) = self.queries.read(page)
            yield from rows
            if len(rows) < CATALOGUE_PAGE_SIZE:
                return
            after = rows[-1][key.name]

    def images(self, generation_id, ids=None):
        statement = select(Image).where(Image.generation_id == generation_id)
        if ids is not None:
            statement = statement.where(Image.image_id.in_(ids))
        return [
            Image.model_validate(row) for row in self._rows(statement, Image.image_id)
        ]

    def associations(self, generation_id, ids=None):
        statement = select(Association).where(
            Association.generation_id == generation_id
        )
        if ids is not None:
            statement = statement.where(Association.image_id.in_(ids))
        return [
            Association.model_validate(row)
            for row in self._rows(statement, Association.id)
        ]

    def _filter_labels(self, generation_id, field):
        entries = (
            func.json_array_elements_text(field)
            .table_valued("value", with_ordinality="position")
            .render_derived()
        )
        # Deduplicate exact labels in SQL. Keep their first source position so
        # Python's Unicode normalization still chooses the same display spelling.
        value = entries.c.value.collate("C")
        first = (
            select(
                value.label("label"),
                Association.id.label("association_id"),
                entries.c.position,
            )
            .select_from(Association)
            .join(entries, true())
            .where(Association.generation_id == generation_id)
            .distinct(value)
            .order_by(value, Association.id, entries.c.position)
            .subquery()
        )
        rows = sorted(
            self._rows(select(first), first.c.label),
            key=lambda row: (row["association_id"], row["position"]),
        )
        return labels(row["label"] for row in rows)

    def filter_options(self, generation_id):
        intervals = func.jsonb_array_elements(
            cast(Association.date_ranges, JSONB)
        ).table_valued(column("value", JSONB))
        (bounds,) = self.queries.read(
            select(
                func.min(cast(intervals.c.value["date_from"].astext, Integer)).label(
                    "date_min"
                ),
                func.max(cast(intervals.c.value["date_to"].astext, Integer)).label(
                    "date_max"
                ),
            )
            .select_from(Association)
            .join(intervals, true())
            .where(Association.generation_id == generation_id)
        )
        return {
            **bounds[0],
            "places": self._filter_labels(generation_id, Association.places),
            "categories": self._filter_labels(generation_id, Association.categories),
        }

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
        rows = self._rows(
            select(Association.image_id)
            .where(
                Association.generation_id == generation_id,
                Association.record_uid == record_uid,
            )
            .distinct(),
            Association.image_id,
        )
        return [row["image_id"] for row in rows]
