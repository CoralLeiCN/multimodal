"""Verify R2 objects before attaching permanent URLs to catalogue rows."""

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import boto3
from botocore.config import Config
from sqlalchemy import inspect, literal, select, update

from app.models import Image


def object_key(relative_path, prefix=""):
    parts = [part for part in (prefix.rstrip("/"), relative_path) if part]
    key = "/".join(parts)
    if (
        not relative_path
        or relative_path.startswith("/")
        or "\\" in key
        or any(part in ("", ".", "..") for part in key.split("/"))
        or any(ord(char) < 32 for char in key)
    ):
        raise ValueError("R2 keys must be relative paths without traversal.")
    return key


def r2_destination(settings):
    endpoint = (settings.r2_endpoint_url or "").rstrip("/")
    bucket = settings.r2_bucket or ""
    if not re.fullmatch(
        r"https://[a-f0-9]{32}(?:\.(?:eu|us|fedramp))?\.r2\.cloudflarestorage\.com",
        endpoint,
    ):
        raise ValueError(
            "Set R2_ENDPOINT_URL to the Cloudflare S3 endpoint without a bucket path."
        )
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
        raise ValueError("Set R2_BUCKET to a valid bucket name.")
    return endpoint, bucket


def r2_object_url(settings, relative_path):
    """Build the migrated object's permanent URL without contacting R2."""
    endpoint, bucket = r2_destination(settings)
    key = object_key(relative_path, settings.r2_prefix)
    return f"{endpoint}/{bucket}/{quote(key, safe='/')}"


def r2_client(settings):
    endpoint, _ = r2_destination(settings)
    if not settings.r2_access_key_id or not settings.r2_secret_access_key:
        raise ValueError("Set the R2 access key ID and secret access key.")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="auto",
        aws_access_key_id=settings.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=10,
            read_timeout=30,
            max_pool_connections=16,
            retries={"mode": "standard", "max_attempts": 3},
        ),
    )


def plan_r2_urls(engine, settings, client):
    """Read all generations and verify the SHA-256 metadata written by upload."""
    _endpoint, bucket = r2_destination(settings)
    table = Image.__table__
    with engine.connect() as connection:
        url_column = (
            table.c.r2_url
            if "r2_url"
            in {c["name"] for c in inspect(connection).get_columns("images")}
            else literal(None).label("r2_url")
        )
        rows = (
            connection.execute(
                select(
                    table.c.generation_id,
                    table.c.image_id,
                    table.c.relative_path,
                    table.c.checksum,
                    url_column,
                ).order_by(table.c.generation_id, table.c.image_id)
            )
            .mappings()
            .all()
        )

    def verify(row):
        key = object_key(row["relative_path"], settings.r2_prefix)
        url = r2_object_url(settings, row["relative_path"])
        if row["r2_url"] not in (None, url):
            raise ValueError("An image already references a different R2 URL.")
        remote = client.head_object(Bucket=bucket, Key=key)
        if remote.get("Metadata", {}).get("sha256") != row["checksum"]:
            raise ValueError(
                f"R2 SHA-256 metadata does not match image {row['image_id']}."
            )
        return {**row, "new_r2_url": url}

    with ThreadPoolExecutor(max_workers=16) as pool:
        return list(pool.map(verify, rows))


def apply_r2_urls(engine, plan):
    """Commit the verified plan atomically; refuse concurrent catalogue changes."""
    table = Image.__table__
    updated = 0
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL lock_timeout = '15s'")
        for row in plan:
            result = connection.execute(
                update(table)
                .where(
                    table.c.generation_id == row["generation_id"],
                    table.c.image_id == row["image_id"],
                    table.c.relative_path == row["relative_path"],
                    table.c.checksum == row["checksum"],
                    table.c.r2_url.is_not_distinct_from(row["r2_url"]),
                )
                .values(r2_url=row["new_r2_url"])
            )
            if result.rowcount != 1:
                raise ValueError(
                    "Catalogue changed during R2 verification; rolled back."
                )
            updated += row["r2_url"] != row["new_r2_url"]
    return updated
