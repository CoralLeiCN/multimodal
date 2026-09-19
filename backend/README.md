# Collection search backend

FastAPI and Pydantic expose the collection at `/api/v1`. Neon PostgreSQL tracks selected
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
Settings load `.env`, then `.env.local`; process environment variables take precedence. Indexing sends selected image bytes to
Google; text and uploaded-image searches send the query to Google. A run of 50
uncached images makes 50 embedding requests, with up to three attempts on
temporary failures. “Find similar” uses the stored Qdrant vector.

The server applies the Alembic migration on startup. The ingestion command also
applies it before creating a run. Set `DATABASE_URL` for PostgreSQL and
`DATABASE_URL_UNPOOLED` for direct migration and writer-lock connections. Neon
pooled URLs use the direct hostname automatically if the latter is omitted.
`DATABASE_URL` is required for the app and indexing. Missing configuration fails
with a setup message instead of creating a catalogue file.

`SEARCH_DATA_DIR` defaults to `data/search/`. It contains the local SQLite
`embedding_cache.sqlite3`, selection snapshots, and run reports. Existing cache
files in that folder are reused. If an older setup used a custom `SQLITE_PATH`,
set `SEARCH_DATA_DIR` to that file's parent directory to retain its cache.
SQLite remains only as the local embedding cache; vectors stay out of Neon.
Use `IMAGE_ROOT`, `QDRANT_URL`, `QDRANT_API_KEY`, `EMBEDDING_MODEL`, and
`EMBEDDING_DIMENSIONS` for the other settings. Relative paths resolve from the
repository root.

Apply catalogue migrations explicitly with:

```sh
uv run --package multimodal-backend alembic -c backend/alembic.ini upgrade head
```

Cache writes commit before ingestion records an image as embedded, so retries
reuse the committed vector after an interruption.

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

To resolve a collection record ID such as `co25823` to image UUIDs, follow the
[collection ID lookup procedure](../docs/collection_id_lookup.md). This uses an
exact PostgreSQL lookup in the active catalogue. The image endpoints above accept
generated image UUIDs; collection ID lookup currently runs outside the API.

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
before selecting the result limit. Browsing uses PostgreSQL JSONB predicates.
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

## Logfire tracing

Pydantic Logfire records API requests and child spans for search, Gemini
embeddings, Qdrant operations, and catalogue result lookups. Indexing records runs,
image attempts, retries, and completion counts. The services are named
`multimodal-api` and `multimodal-indexer`.

The API and indexing pipeline have separate Logfire destinations. The API uses
`LOGFIRE_TOKEN` from the ignored root `.env`, or the credentials saved by setup in
`.logfire/logfire_credentials.json`. Indexing uses `LOGFIRE_INDEXER_TOKEN` or
`.logfire/indexer/logfire_credentials.json`. Save a separate project's credentials
with the setup CLI's `--data-dir .logfire/indexer` option to connect indexing.
The whole `.logfire/` directory is ignored by Git. Keep each project's region
consistent when setting up credentials. See
[Logfire setup](https://pydantic.dev/docs/logfire/get-started/).

| Setting | Default | Purpose |
| --- | --- | --- |
| `LOGFIRE_TOKEN` | Unset | API project write token; `.logfire/` credentials are used when omitted. |
| `LOGFIRE_INDEXER_TOKEN` | Unset | Indexing project write token; `.logfire/indexer/` credentials are used when omitted. |
| `LOGFIRE_SEND_TO_LOGFIRE` | `if-token-present` | Export when credentials are available; set `false` to disable cloud export. |
| `LOGFIRE_ENVIRONMENT` | `development` | Label the environment in Logfire. |

Indexing ignores the shared SDK destination variables `LOGFIRE_TOKEN`,
`LOGFIRE_API_KEY`, and `LOGFIRE_BASE_URL` during tracing configuration so an API
connection cannot override its own project. It does not fall back to API credentials.

Restart the API after configuring credentials. Run a request and check the
project's Live view for `multimodal-api`. Indexing flushes pending telemetry before
exit. The Python tests and OpenAPI export disable cloud telemetry.

Traces include operation names, durations, embedding model and dimensions, run
and image identifiers, attempt numbers, and error types or safe application error
codes. Request arguments, query-string values, headers, image bytes, embedding
vectors, and raw exception messages are excluded. Application events use
structured Logfire logs.

## Permanent collection setting

`QDRANT_COLLECTION_NAME` selects a permanent collection for ingestion. Omit it
for a separate collection per generation. Use an existing collection's exact name
and matching catalogue database to extend it. Each sample upserts selected images
and keeps earlier images. Model and dimensions must match the existing index.
Stop the API during in-place updates and restart it after successful ingestion.
Failed updates require resume before that collection becomes available again.
See [setup and recovery](../cronjob/README.md#permanent-collection).

## Image creation agent

The `/api/v1/agent` routes expose authenticated brand profiles, uploads, immutable
versions, asynchronous runs, SSE progress, cancellation, and follow-up edits.
They use a separate database and storage namespace from the collection catalogue.
The standalone factory `app.services.agent.application:create_agent_app` starts
without Qdrant or collection migrations. See [setup and API behavior](../docs/image_agent_setup.md)
and the [design](../docs/image_agent_design.md).


The conversation backend supports brand-bound chats, collection image IDs, and
follow-up edits in Modal sandboxes. See [Brand chat agent backend](../docs/chat_agent_backend.md)
for endpoints, configuration, recovery, and verification. The collection chat sidebar connects to these endpoints and supports saved
conversations, task cancellation, image downloads, and follow-up edits.


Gemini Flash handles chat and compiles image tasks in the trusted backend. Modal
sandboxes execute source lookup, Nano Banana generation, evaluation, and result return.
Current user instructions override conflicting brand defaults for that task; the
saved brand remains unchanged. Pure chat does not create a sandbox. Collection image
IDs can resolve through the local catalogue or a configured trusted online API.
## Neon collaboration

See the [collaborator setup](../docs/neon_setup.md). PostgreSQL stores the catalogue and
ledger; Qdrant remains the vector store. Indexing and metadata refresh use a
shared PostgreSQL advisory lock so two collaborators cannot run these commands
at once. The local embedding cache and reports remain under `data/search/`.
Database traces use `catalogue.*` names for either database engine.

The app reads `.env`, then `.env.local`; process environment variables override
both. Neon connection credentials and the `.neon` checkout link remain ignored
by Git. Each collaborator links their own checkout.

`neon.ts` declares an empty service policy. `bun run neon deploy` applies that
policy; host FastAPI and the frontend separately. The Codex MCP connection uses
OAuth and may require a separate sign-in when first used.

### Import an existing SQLite catalogue

Do this once, after testing on a temporary Neon branch. Stop writers to the
source catalogue and its Qdrant collection during the import. Keep the original
SQLite file as a rollback copy. The destination must have no catalogue rows.

```sh
uv run --package multimodal-backend scripts/migrate_catalogue_to_neon.py \
  --source /absolute/path/to/catalog.sqlite3
```

For an isolated destination, pull its connection settings first:

```sh
bun run neon env pull --branch BRANCH --file .env.neon-test --service postgres
```

Then add `--env-file .env.neon-test` to the import command.
That file overrides `.env` for this command and does not change the app's branch.

The importer requires SQLite migration `0003`. It checks database integrity and
foreign keys, active generation readiness and count, embedding configuration,
and every active image's Qdrant vector and payload. It then applies Alembic to
PostgreSQL and copies generations, images, associations, ingestion records, and
active state in one transaction. It locks destination tables, refuses existing
rows, and compares every table's ordered contents before committing. A failed
copy rolls back all inserted rows; an empty migrated schema may remain.
It preserves identifiers and attribution and makes no Qdrant writes or embedding
requests. The cache is excluded because it lives in a separate database.

After import, check `/api/v1/status`, browse with filters, open an image, and use
“Find similar.” Each collaborator must use the matching Qdrant collection and
embedding settings. A shared catalogue does not distribute thumbnails.

### Indexing and maintenance

Run the existing indexing and metadata refresh commands with the Neon settings.
They acquire a PostgreSQL advisory lock so another collaborator cannot update
the same database through these commands concurrently. Local dry runs need no
Neon connection. Use one indexing machine for each run; local caches are not
shared. A collaborator can resume using the shared ledger and their local image
files; already verified Qdrant points are reused.

Selection snapshots, embedding cache, and reports use `SEARCH_DATA_DIR`. Coordinate permanent collection
updates and stop the API while updating that collection. Use Neon backup facilities
or PostgreSQL tools for the shared database and back up Qdrant separately.

For development that writes data, use a separate Neon branch and Qdrant
collection. A Neon branch alone does not isolate external Qdrant writes.

Backend database tests require `TEST_POSTGRES_URL` pointing to a disposable
PostgreSQL database's direct connection URL. They never use the app's
`DATABASE_URL`. Each test creates and removes an isolated schema; caches remain
in temporary local SQLite files. Qdrant and embeddings use local fakes.

```sh
uv run pytest
uv run ruff check .
```
