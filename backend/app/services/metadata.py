"""Query labels and equivalent filters for prepared catalogue metadata."""

import unicodedata

from qdrant_client import models
from sqlalchemy import Integer, cast, column, func, select
from sqlalchemy.dialects.postgresql import JSONB

from app.models import Image
from app.schemas import MetadataFilters

PAYLOAD_SCHEMA_VERSION = 1
PAYLOAD_INDEXES = {
    "metadata[].place": models.PayloadSchemaType.KEYWORD,
    "metadata[].category": models.PayloadSchemaType.KEYWORD,
    "metadata[].date_from": models.PayloadSchemaType.INTEGER,
    "metadata[].date_to": models.PayloadSchemaType.INTEGER,
}


def normalize_label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def labels(values) -> list[str]:
    unique = {}
    for value in values:
        if isinstance(value, str) and normalize_label(value):
            unique.setdefault(normalize_label(value), value.strip())
    return sorted(unique.values(), key=normalize_label)


def qdrant_filter(filters: MetadataFilters, exclude: str | None = None):
    conditions = []
    for key in ("place", "category"):
        values = getattr(filters, key)
        if values:
            conditions.append(
                models.FieldCondition(key=key, match=models.MatchAny(any=values))
            )
    if filters.date_from is not None:
        conditions.append(
            models.FieldCondition(
                key="date_to", range=models.Range(gte=filters.date_from)
            )
        )
    if filters.date_to is not None:
        conditions.append(
            models.FieldCondition(
                key="date_from", range=models.Range(lte=filters.date_to)
            )
        )
    must = (
        [
            models.NestedCondition(
                nested=models.Nested(
                    key="metadata", filter=models.Filter(must=conditions)
                )
            )
        ]
        if conditions
        else []
    )
    must_not = [models.HasIdCondition(has_id=[exclude])] if exclude else []
    return models.Filter(must=must, must_not=must_not) if must or must_not else None


def catalogue_filter(filters: MetadataFilters):
    rows = (
        func.jsonb_array_elements(cast(Image.filter_metadata, JSONB))
        .table_valued(column("value", JSONB))
        .alias("metadata")
    )
    conditions = []
    for key in ("place", "category"):
        values = getattr(filters, key)
        if values:
            entries = func.jsonb_array_elements_text(rows.c.value[key]).table_valued(
                "value"
            )
            conditions.append(
                select(1)
                .select_from(entries)
                .where(entries.c.value.in_(values))
                .correlate(rows)
                .exists()
            )
    if filters.date_from is not None:
        conditions.append(
            cast(rows.c.value["date_to"].astext, Integer) >= filters.date_from
        )
    if filters.date_to is not None:
        conditions.append(
            cast(rows.c.value["date_from"].astext, Integer) <= filters.date_to
        )
    return (
        select(1).select_from(rows).where(*conditions).correlate(Image).exists()
        if conditions
        else True
    )
