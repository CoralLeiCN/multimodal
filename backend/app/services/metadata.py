"""Catalogue labels, conservative year ranges, and equivalent database filters."""

import re
import unicodedata
from calendar import monthrange

from qdrant_client import models
from sqlalchemy import func, select

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


def year(value) -> int | None:
    # Source bounds are years or ISO dates. Free text is deliberately not guessed.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    match = re.fullmatch(r"(-?\d{1,4})(?:-(\d{2})-(\d{2}))?", str(value).strip())
    if match and (number := int(match[1])) != 0:
        if match[2]:
            month, day = int(match[2]), int(match[3])
            if not 1 <= month <= 12 or not 1 <= day <= monthrange(number, month)[1]:
                return None
        return number
    return None


def extract_metadata(record: dict) -> dict:
    creation = record.get("creation") or {}
    dates = creation.get("date") or record.get("date") or []
    ranges = set()
    for date in dates:
        if date.get("from") is not None or date.get("to") is not None:
            start, end = year(date.get("from")), year(date.get("to"))
        else:
            value = date.get("value")
            # Treat c.YYYY as that year for filtering; retain source display text.
            if isinstance(value, str) and (
                match := re.fullmatch(r"c\.\s*(-?\d{1,4})", value.strip(), re.IGNORECASE)
            ):
                value = match[1]
            start = end = year(value)
        if start is not None and end is not None and start <= end:
            ranges.add((start, end))
    return {
        "places": labels(
            (place.get("summary") or {}).get("title")
            or next(
                (
                    name.get("value")
                    for name in place.get("name") or []
                    if name.get("value")
                ),
                "",
            )
            for place in creation.get("place") or []
        ),
        "categories": labels(
            category.get("name") or category.get("value")
            for category in record.get("category") or []
        ),
        "date_ranges": [
            {"date_from": start, "date_to": end} for start, end in sorted(ranges)
        ],
    }


def filter_rows(associations: list[dict]) -> list[dict]:
    """One row per record and interval; never bridge gaps or cross records."""
    rows = []
    for association in associations:
        base = {
            "record_uid": association["record_uid"],
            "place": sorted(
                {normalize_label(value) for value in association.get("places", [])}
            ),
            "category": sorted(
                {normalize_label(value) for value in association.get("categories", [])}
            ),
        }
        for interval in association.get("date_ranges") or [{}]:
            row = {**base, **interval}
            if row not in rows:
                rows.append(row)
    return rows


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


def sqlite_filter(filters: MetadataFilters):
    """SQLite JSON predicates use the same record/interval boundaries as Qdrant."""
    rows = func.json_each(Image.filter_metadata).table_valued("value").alias("metadata")
    conditions = []
    for key in ("place", "category"):
        values = getattr(filters, key)
        if values:
            entries = func.json_each(
                func.json_extract(rows.c.value, f"$.{key}")
            ).table_valued("value")
            conditions.append(
                select(1)
                .select_from(entries)
                .where(entries.c.value.in_(values))
                .correlate(rows)
                .exists()
            )
    if filters.date_from is not None:
        conditions.append(
            func.json_extract(rows.c.value, "$.date_to") >= filters.date_from
        )
    if filters.date_to is not None:
        conditions.append(
            func.json_extract(rows.c.value, "$.date_from") <= filters.date_to
        )
    return (
        select(1).select_from(rows).where(*conditions).correlate(Image).exists()
        if conditions
        else True
    )
