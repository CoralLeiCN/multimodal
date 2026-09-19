"""Read an exact collection image ID without Qdrant or arbitrary URL access."""

import hashlib
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from app.services.agent.storage import AgentError


def resolve_record_images(record_uid, engine=None):
    """Resolve every distinct image in a single read-only catalogue snapshot."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from app.core.config import Settings
    from app.core.db import make_engine

    owned = engine is None
    try:
        engine = engine if engine is not None else make_engine(Settings())
        with engine.connect() as connection, connection.begin():
            if engine.dialect.name == "postgresql":
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                connection.exec_driver_sql("SET LOCAL statement_timeout = '15s'")
            generation = (
                connection.execute(
                    text(
                        "SELECT g.id,g.status FROM service_state s "
                        "JOIN index_generations g ON g.id=s.active_generation WHERE s.id=1"
                    )
                )
                .mappings()
                .first()
            )
            if not generation or generation["status"] != "ready":
                raise AgentError(
                    "collection_unavailable",
                    "No active ready catalogue is available.",
                    503,
                )
            rows = (
                connection.execute(
                    text(
                        "SELECT DISTINCT i.image_id,i.title,i.relative_path,i.checksum,i.generation_id "
                        "FROM image_associations a JOIN images i "
                        "ON i.generation_id=a.generation_id AND i.image_id=a.image_id "
                        "WHERE a.generation_id=:generation AND a.record_uid=:record ORDER BY i.image_id"
                    ),
                    {"generation": generation["id"], "record": record_uid},
                )
                .mappings()
                .all()
            )
            result = []
            for row in rows:
                sources = (
                    connection.execute(
                        text(
                            "SELECT record_uid,image_uid,title,licence,copyright,credit "
                            "FROM image_associations WHERE generation_id=:generation AND image_id=:image ORDER BY id"
                        ),
                        {"generation": generation["id"], "image": row["image_id"]},
                    )
                    .mappings()
                    .all()
                )
                result.append({**row, "associations": [dict(item) for item in sources]})
            return result
    except (SQLAlchemyError, ValueError):
        raise AgentError(
            "collection_unavailable",
            "The shared PostgreSQL catalogue cannot be read. Check DATABASE_URL.",
            503,
        ) from None
    finally:
        if owned and engine is not None:
            engine.dispose()


def read_collection_image(settings, image_id):
    if re.fullmatch(r"co[0-9]+", image_id):
        matches = resolve_record_images(image_id)
        if not matches:
            raise AgentError(
                "collection_record_missing",
                f"Collection record {image_id} has no images in the active indexed catalogue. It may still exist in the full museum collection.",
                404,
            )
        if len(matches) > 1:
            choices = "; ".join(
                f"{item['image_id']} ({item['title']})" for item in matches
            )
            raise AgentError(
                "collection_record_ambiguous",
                f"Collection record {image_id} has multiple images. Ask the user to select an image UUID: {choices}",
                409,
            )
        match = matches[0]
        if settings.collection_api_url:
            data, source = read_online_image(settings, match["image_id"])
        else:
            data, source = read_local_image(settings, match, match["associations"])
        return data, {**source, "requested_record_uid": image_id}
    if settings.collection_api_url:
        return read_online_image(settings, image_id)
    database = Path(settings.collection_database).resolve()
    if not database.is_file():
        raise AgentError(
            "collection_unavailable", "The collection catalogue is unavailable.", 503
        )
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            image = db.execute(
                "SELECT i.* FROM images i JOIN service_state s ON s.active_generation=i.generation_id "
                "JOIN index_generations g ON g.id=i.generation_id "
                "WHERE s.id=1 AND g.status='ready' AND i.image_id=?",
                (image_id,),
            ).fetchone()
            if image is None:
                raise AgentError(
                    "image_missing",
                    "No current collection image has this image_id.",
                    404,
                )
            sources = [
                dict(row)
                for row in db.execute(
                    "SELECT record_uid,image_uid,title,licence,copyright,credit FROM image_associations "
                    "WHERE generation_id=? AND image_id=? ORDER BY id",
                    (image["generation_id"], image_id),
                )
            ]
    except sqlite3.Error:
        raise AgentError(
            "collection_unavailable", "The collection catalogue cannot be read.", 503
        ) from None
    return read_local_image(settings, image, sources)


def read_local_image(settings, image, sources):
    root = Path(settings.collection_image_root).resolve()
    path = (root / image["relative_path"]).resolve()
    if not path.is_relative_to(root):
        raise AgentError(
            "invalid_collection_path", "Collection image path is invalid.", 422
        )
    try:
        with path.open("rb") as stream:
            data = stream.read(settings.max_image_bytes + 1)
    except OSError:
        raise AgentError(
            "image_unavailable", "The collection image file is unavailable.", 404
        ) from None
    if len(data) > settings.max_image_bytes:
        raise AgentError(
            "image_too_large", "The collection image exceeds the input limit.", 413
        )
    if hashlib.sha256(data).hexdigest() != image["checksum"]:
        raise AgentError(
            "image_changed",
            "The collection file changed; refresh the catalogue first.",
            409,
        )
    return data, {
        "type": "collection",
        "image_id": image["image_id"],
        "generation_id": image["generation_id"],
        "title": image["title"],
        "associations": sources,
    }


def read_online_image(settings, image_id):
    import json
    import re
    from urllib.parse import quote, urlparse

    import httpx

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", image_id):
        raise AgentError(
            "image_missing", "Use the exact image_id from a search result.", 404
        )
    origin = settings.collection_api_url.rstrip("/")
    parsed = urlparse(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        raise AgentError(
            "collection_unavailable",
            "Configure a trusted HTTPS collection API origin.",
            503,
        )
    headers = {}
    if settings.collection_api_token:
        headers["Authorization"] = (
            "Bearer " + settings.collection_api_token.get_secret_value()
        )

    def fetch(client, path, limit):
        with client.stream("GET", origin + path) as response:
            if response.status_code == 404:
                raise AgentError(
                    "image_missing", "No online collection image has this ID.", 404
                )
            response.raise_for_status()
            chunks = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise AgentError(
                        "image_too_large",
                        "The collection response exceeds its limit.",
                        413,
                    )
                chunks.append(chunk)
            return b"".join(chunks)

    path = "/api/v1/images/" + quote(image_id, safe="")
    try:
        with httpx.Client(
            timeout=20, follow_redirects=False, headers=headers
        ) as client:
            metadata = json.loads(fetch(client, path, 256 * 1024))
            if metadata.get("image_id") != image_id or not isinstance(
                metadata.get("associations", []), list
            ):
                raise ValueError("Mismatched image metadata")
            content = fetch(client, path + "/file", settings.max_image_bytes)
        return content, {
            "type": "collection",
            "image_id": image_id,
            "origin": origin,
            "title": str(metadata.get("title", "")),
            "associations": [
                {
                    k: a.get(k, "")
                    for k in (
                        "record_uid",
                        "image_uid",
                        "title",
                        "licence",
                        "copyright",
                        "credit",
                        "source_url",
                    )
                }
                for a in metadata.get("associations", [])
            ],
        }
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise AgentError(
            "collection_unavailable", "The online collection could not be read.", 503
        ) from None
