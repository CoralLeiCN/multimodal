# Look up images by collection ID

Use this route when a user supplies an SMG collection record ID such as
`co25823` and asks for its images. Resolve the ID through the shared Neon PostgreSQL
catalogue using exact equality on `image_associations.record_uid`.

## Identifier mapping

| Identifier | Location and meaning |
| --- | --- |
| Collection record ID, such as `co25823` | Bronze `@admin.uid`, silver/gold `uid`, and PostgreSQL `image_associations.record_uid` identify the same source record. |
| Object number | Silver/gold `identifier` and PostgreSQL `catalogue_identifiers` hold catalogue references such as accession numbers. |
| Source image ID | Bronze `multimedia[*].@admin.uid` is preserved as PostgreSQL `image_uid`. |
| Indexed image UUID | PostgreSQL `images.image_id` and the Qdrant point ID are generated from the thumbnail location. Image API routes accept this UUID. |

A record can have several images, and an image can have several source
associations. Return every distinct matching image in the active ready generation.
Scope joins by both `generation_id` and `image_id` to keep historical runs separate.

## Agent lookup procedure

1. Read the configured catalogue using `Settings` and `make_engine` so the Neon
   `DATABASE_URL` and image paths follow the backend configuration. Use a
   read-only transaction with a consistent snapshot.
2. Resolve `service_state.id = 1` to `index_generations.id` and require status
   `ready`. Report an unavailable catalogue separately from an unmatched ID.
3. Match the supplied ID exactly in `image_associations.record_uid`, within that
   generation, and join to `images`. Bind user input as a SQL parameter.
4. Return each image's UUID, title, and local path. When the backend is running,
   use the returned UUID with `GET /api/v1/images/{image_id}` for metadata and
   attribution, and `GET /api/v1/images/{image_id}/file` for image bytes. Use the
   backend's configured host and port.
5. If there are no matches, report that the record has no images in the active
   indexed catalogue. This does not establish whether it exists in the full SMG
   dataset. To investigate source coverage, match gold `uid` or bronze
   `@admin.uid` separately and label those results as source records.

The current text search endpoint embeds its query with Gemini. Collection ID
lookup uses the PostgreSQL procedure below; there is no dedicated collection ID API
endpoint or search-box detection. It requires no Gemini request, Qdrant request,
or reindexing. The image detail and file endpoints also read from PostgreSQL and local
files. Local thumbnails must be present to open the images. Preserve the source
association's attribution and rights when presenting them; see
[the data specification](../data_spec.md).

## Run a lookup

Run from the repository root after installing the backend dependencies. Replace
`co25823` with the requested record ID:

```sh
uv run --package multimodal-backend python - co25823 <<'PY'
import json
import sys

from app.core.config import Settings
from app.core.db import make_engine
from sqlalchemy import text

settings = Settings()
engine = make_engine(settings)
record_uid = sys.argv[1].strip()
connection = engine.connect()
try:
    connection.begin()
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    generation = connection.execute(text(
        """
        SELECT g.id, g.status
        FROM service_state AS s
        JOIN index_generations AS g ON g.id = s.active_generation
        WHERE s.id = 1
        """
    )).mappings().first()
    if generation is None or generation["status"] != "ready":
        raise SystemExit("No active ready catalogue is available.")
    rows = connection.execute(text(
        """
        SELECT DISTINCT i.image_id, i.title, i.relative_path
        FROM image_associations AS a
        JOIN images AS i
          ON i.generation_id = a.generation_id AND i.image_id = a.image_id
        WHERE a.generation_id = :generation_id AND a.record_uid = :record_uid
        ORDER BY i.image_id
        """),
        {"generation_id": generation["id"], "record_uid": record_uid},
    ).mappings().all()
    images = []
    for row in rows:
        image = dict(row)
        path = (settings.absolute(settings.image_root) / row["relative_path"]).resolve()
        if not path.is_relative_to(settings.absolute(settings.image_root)):
            raise SystemExit("An image path is outside the configured image root.")
        image["local_path"] = str(path)
        image["local_file_exists"] = path.is_file()
        image["metadata_url"] = f"/api/v1/images/{row['image_id']}"
        image["image_url"] = f"/api/v1/images/{row['image_id']}/file"
        images.append(image)
    print(json.dumps({
        "record_uid": record_uid,
        "index_version": generation["id"],
        "matching_images": len(images),
        "images": images,
    }, indent=2, ensure_ascii=False))
finally:
    connection.close()
    engine.dispose()
PY
```

An empty `images` array means no match in the active ready catalogue. A missing
connection or schema is a setup error. Check the
[Neon setup](neon_setup.md) and `DATABASE_URL` before retrying. This lookup makes
no schema or data changes.


The chat executor accepts `co` collection record IDs as well as image UUIDs.
For a record ID, the trusted gateway queries `DATABASE_URL` using an exact bound
`record_uid` match in a read-only, repeatable-read transaction. All distinct images
from the active ready generation are returned. A single match is resolved to its
UUID before loading from the configured collection API or local image root.
Multiple matches are returned to chat for selection; the agent does not choose an
arbitrary image. Missing records and unavailable catalogues have separate errors.
Imported assets retain `requested_record_uid`, the resolved image UUID, and attribution.
The gateway process needs the catalogue connection even when image bytes use the
online API. Restart the API and worker after updating this code.

If the active generation is `building`, lookup reports that indexing is awaiting
completion or final checks. This is separate from a database connection failure.
An `indexed` ingestion ledger alone is insufficient to publish a generation:
the indexer must verify vector counts and payloads before marking it `ready`.
Do not override that state while another session owns the ingestion writer lock.
