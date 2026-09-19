# Collection search backend

FastAPI and Pydantic expose the collection at `/api/v1`. SQLite tracks selected
images and completed ingestion; Qdrant stores their Gemini embeddings. The code
uses the directory conventions of the
[Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template).

Run commands from the repository root:

```sh
uv sync --locked --all-packages
docker compose up -d qdrant
uv run --package multimodal-backend cronjob/index_images.py --limit 50 --scan-limit 1000
uv run --package multimodal-backend uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Set `GEMINI_API_KEY` in the ignored root `.env` file before indexing or searching.
Settings load that file automatically. Indexing sends selected image bytes to
Google; text and uploaded-image searches send the query to Google. A run of 50
uncached images makes 50 embedding requests, with up to three attempts on
temporary failures. “Find similar” uses the stored Qdrant vector.

The server applies the Alembic migration on startup. The ingestion command also
applies it before creating a run. The SQLite path defaults to
`data/search/catalog.sqlite3`; Qdrant persists under `data/search/qdrant/`.
Use `SQLITE_PATH`, `IMAGE_ROOT`, `QDRANT_URL`, `QDRANT_API_KEY`, `EMBEDDING_MODEL`,
and `EMBEDDING_DIMENSIONS` to override settings. Relative filesystem settings are
resolved against the repository root.

Embeddings default to 1,536 dimensions. Copy the Qdrant Cloud endpoint and API
key into `QDRANT_URL` and `QDRANT_API_KEY` in `.env`; skip the Docker command
when using Cloud. For local Docker, use `QDRANT_URL=http://127.0.0.1:6333` and
an empty `QDRANT_API_KEY`. The `.env.example` values are placeholders.
Changing dimensions requires a new indexing run and an API restart; existing
768-dimensional vectors cannot be used with 1,536-dimensional queries.

The API pins a ready index once it becomes available. Restart the API after
publishing a replacement index. The frontend build is served at `/` when
`frontend/dist/` exists at backend startup.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/status` | Ready state, indexed count, and search availability. |
| `GET /api/v1/filters` | Available places, categories, and minimum/maximum years in the active sample. |
| `GET /api/v1/images?limit=24` | Browse with an optional `cursor` and metadata filters; includes `matching_images`. |
| `GET /api/v1/images/{image_id}` | Image metadata and attribution. |
| `GET /api/v1/images/{image_id}/file` | Local image bytes. |
| `POST /api/v1/search/text` | Text query as JSON. |
| `POST /api/v1/search/image` | Multipart image query. |
| `POST /api/v1/images/{image_id}/similar` | Similar images using a stored vector. |

Example text request:

```sh
curl http://127.0.0.1:8000/api/v1/search/text \
  -H 'Content-Type: application/json' \
  -d '{"query":"a brass microscope","limit":5,"filters":{"date_from":1850,"date_to":1900,"place":["London"],"category":["Optics"]}}'
```

Example uploaded-image request:

```sh
curl http://127.0.0.1:8000/api/v1/search/image \
  -F 'image=@/path/to/query.jpg' -F 'limit=5' \
  -F 'date_from=1850' -F 'place=London' -F 'category=Optics'
```

Dates are inclusive year ranges and match overlapping source ranges. Omit either
bound for an open-ended query. Negative years mean BCE; zero and reversed ranges
are invalid. Place means creation place. Place/category matches are exact after
Unicode normalization, whitespace normalization, and case folding. Multiple
values in a field use OR; different fields use AND within the same catalogue
association. Missing values fail a filter on that field. Use labels returned by
`GET /api/v1/filters`, which lists the entire active sample's options.

Text and similar-image JSON requests accept a `filters` object as above. Browsing
accepts `date_from`, `date_to`, and repeated `place`/`category` query parameters.
Image uploads accept those fields as multipart form fields. Up to 20 values per
field and 300 characters per value are allowed. Filters are applied inside Qdrant
before selecting the result limit. Browsing uses equivalent SQLite predicates.
Browse cursors are bound to their filter set; change filters by starting a new
page sequence. Unknown dates do not match date-restricted searches.

New ingestion runs create four Qdrant indexes automatically: keyword indexes on
`metadata[].place` and `metadata[].category`, and integer indexes on
`metadata[].date_from` and `metadata[].date_to`. To update an existing sample,
use [the metadata refresh command](../cronjob/README.md#refresh-search-filter-metadata).
Stop the API before refreshing a published sample and restart after completion.
This command updates payloads without reading images or requesting embeddings.

Interactive API documentation is at `http://127.0.0.1:8000/docs`; the schema is
at `/api/v1/openapi.json`. JSON errors include `code` and `message`. Images are
limited to JPEG or PNG, 10 MiB, and 20 million pixels. Empty indexes and unavailable
providers return `503`; Gemini quota exhaustion returns `429`.

Run backend checks with:

```sh
uv run pytest backend/tests cronjob
uv run ruff check backend cronjob scripts
```

Tests use an isolated in-memory Qdrant client and deterministic embeddings. They
exercise actual Qdrant ranking, ingestion recovery, publication, image validation,
and API behavior without Gemini calls.

To verify payload indexes and nested filtering on the running local Qdrant server:

```sh
TEST_QDRANT_URL=http://127.0.0.1:6333 uv run pytest backend/tests/test_qdrant_server.py
```

This test creates and deletes its own temporary collection with synthetic vectors.
