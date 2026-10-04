# Collection search backend

FastAPI and Pydantic expose the collection at `/api/v1`. Neon PostgreSQL tracks selected
images and completed ingestion; Qdrant stores their SigLIP 2 embeddings by default. The code
uses the directory conventions of the
[Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template).

The root `Dockerfile` packages this backend and the compiled React frontend for
one Hugging Face Docker Space on `0.0.0.0:7860`. See the
[deployment gap checklist](../docs/huggingface_spaces.md) for local container
validation, live Neon compatibility checks, and the remaining hosted connection
and operational checks.

Run commands from the repository root:

```sh
uv sync --locked --all-packages
docker compose up -d qdrant
uv run --package multimodal-backend cronjob/index_images.py --limit 50 --scan-limit 1000
uv run --package multimodal-backend uvicorn app.main:app --host 127.0.0.1 --port 8000
```

After selecting images once, run `make index` or the indexing script without
`--limit` to reuse the saved catalogue and bypass metadata scanning and sampling.
It resumes unfinished work and skips verified indexed images. The configured
`QDRANT_COLLECTION_NAME` selects the saved generation; otherwise the catalogue
must contain exactly one generation. Use `--resume RUN_ID` when it contains
several, or an explicit `--limit` to select a new sample. Selection flags and
`--prepare-only` require `--limit`; `make preview-index` supplies 50 by default.

Search defaults to `google/siglip2-base-patch16-224` on CPU with 768-dimensional
vectors. No provider API key is needed. Settings load `.env`, then `.env.local`;
process environment variables take precedence. The first embedding operation
loads the pinned model revision into `EMBEDDING_MODEL_CACHE` (default
`data/models/`). The weights occupy about 1.5 GB. Prepare this cache before using
`HF_HUB_OFFLINE=1` or mounting it read-only in Docker.

`EMBEDDING_CPU_THREADS=2` and `EMBEDDING_BATCH_SIZE=8` bound inference work.
A lock serializes model loading and inference per process. Use one Uvicorn worker
for the preview so the model is not duplicated in memory. Images are EXIF-oriented
and converted to RGB before the pinned processor resizes them to 224 pixels;
plain text is padded or truncated to 64 tokens. “Find similar” uses the stored
Qdrant vector. `/api/v1/status` reports `embedding_provider` so the upload notice
reports local processing. Readiness verifies the index; the first embedding
request also verifies that weights can load.

Apply catalogue migrations with `make migrate` before starting or updating the
server. The ingestion command also applies them before creating a run. The search
API performs catalogue reads only. Set `DATABASE_URL` for PostgreSQL and
`DATABASE_URL_UNPOOLED` for direct migration and writer-lock connections. Neon
pooled URLs use the direct hostname automatically if the latter is omitted.
`DATABASE_URL` is required for the app and indexing. Missing configuration fails
with a setup message instead of creating a catalogue file.

`CATALOGUE_TRANSPORT=postgres` uses the existing SQLAlchemy/psycopg connection.
For HF search, set `CATALOGUE_TRANSPORT=neon_http` to read the same catalogue over
HTTPS on port 443. Keep the original Neon PostgreSQL `DATABASE_URL`; the adapter
derives the HTTPS endpoint. Shared SQLAlchemy SELECT expressions remain
parameterized across both transports. Only Neon hosts are supported by HTTPS;
requests use read-only batches with bounded timeouts and no redirects.
The web edition requires `postgres` for conversation transactions and its worker
lock. Migration and indexing commands always use native PostgreSQL, independently
of the search transport. See the [HF configuration](../docs/huggingface_spaces.md#database-connections).

`SEARCH_DATA_DIR` defaults to `data/search/`. It contains the local SQLite
`embedding_cache.sqlite3`, selection snapshots, and run reports. Existing cache
files in that folder are reused. SQLite holds the local embedding cache;
vectors stay in Qdrant.
Use `IMAGE_ROOT`, `QDRANT_URL`, `QDRANT_API_KEY`, `EMBEDDING_MODEL`, and
`EMBEDDING_DIMENSIONS` for the other settings. Relative paths resolve from the
repository root.

Apply catalogue migrations explicitly with:

```sh
uv run --package multimodal-backend alembic -c backend/alembic.ini upgrade head
```

Indexing defaults to `--batch-size 10 --workers 10`: up to 10 image batches in
flight, each holding up to 10 catalogue images. Use
`make index BATCH_SIZE=10 WORKERS=4` to choose the limits. `--batch-size` accepts 1–100; `--workers` accepts
1–10. Use `WORKERS=1` for SigLIP: its adapter serializes CPU inference and
splits each batch into at most `EMBEDDING_BATCH_SIZE` images. Only uncached content
is embedded, with one separate vector per image.
Requests split at 12 MiB of raw image bytes. `BATCH_SIZE=1` sends one image per
request. Both settings may change when resuming a run.

Each worker owns its catalogue sessions. All vectors in a response are validated
and committed to the cache before catalogue updates or Qdrant writes, so retries
reuse completed embeddings after an interruption. Active workers finish before
the run releases its writer lock; publication waits for all images and Qdrant
verification.

SigLIP 2 Base embeddings have 768 dimensions. Copy the Qdrant Cloud endpoint and API
key into `QDRANT_URL` and `QDRANT_API_KEY` in `.env`; skip the Docker command
when using Cloud. Cloud REST supports port 443: set
`QDRANT_URL=https://your-cluster-host:443` explicitly, since the Python client
falls back to 6333 when a URL omits the port. See
[Qdrant's cluster setup guide](https://qdrant.tech/documentation/cloud/create-cluster/).
For local Docker, use `QDRANT_URL=http://127.0.0.1:6333` and
an empty `QDRANT_API_KEY`. The `.env.example` values are placeholders.
The Python client and local Docker server are pinned to Qdrant 1.19.1 to match
the configured Cloud server. Keep these versions aligned when upgrading Qdrant.
Changing the model, revision, dimensions, or preprocessing requires a new index
and an API restart. Use a separate Qdrant collection for a new embedding
configuration; vectors from different models cannot be combined. The model
revision and preprocessing contract participate in the cache/index fingerprint.

The API pins a ready index once it becomes available. Restart the API after
publishing a replacement index. The frontend build is served at `/` when
`frontend/dist/search/` exists at backend startup.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/status` | Ready state, indexed count, and search availability. |
| `GET /api/v1/filters` | Available places, categories, and minimum/maximum years in the active sample. |
| `GET /api/v1/images?limit=24` | Browse with an optional `cursor` and metadata filters; includes `matching_images`. |
| `GET /api/v1/images/{image_id}` | Image metadata and attribution. |
| `GET /api/v1/images/{image_id}/file` | Image bytes from the configured HF mount or local development directory. |
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
models return `503`; exhausted concurrent search capacity returns `429`.
The API limits mutation request bodies before parsing: 256 KiB for JSON and
the configured image byte limit plus 1 MiB for multipart uploads. The default
multipart limit is 11 MiB. Oversized requests return `413` with `body_too_large`,
including streamed requests without a `Content-Length` header. Individual image
size and pixel checks still apply after parsing. Startup failures release any
database engines and search clients already opened.

Run backend checks with:

```sh
uv run --all-packages --extra agent --extra web pytest backend/tests cronjob
uv run ruff check backend cronjob scripts
```

Tests use an isolated in-memory Qdrant client and deterministic embeddings. They
exercise actual Qdrant ranking, ingestion recovery, publication, image validation,
and API behavior without downloading models or calling external embedding providers.

To verify payload indexes and nested filtering on the running local Qdrant server:

```sh
TEST_QDRANT_URL=http://127.0.0.1:6333 uv run --all-packages --extra agent --extra web pytest backend/tests/test_qdrant_server.py
```

This test creates and deletes its own temporary collection with synthetic vectors.

## Logfire tracing

Pydantic Logfire records API requests and child spans for search, embedding
calls, Qdrant operations, and catalogue result lookups. Indexing records runs,
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

## Optional chat and image generation

This is the separately retained Image Studio prototype. The first web release uses Codex
for collection exploration and defers image generation. Its remaining hosted checks
are recorded in the [version decision](../docs/product_versions.md).

Install the optional dependencies with `uv sync --locked --all-packages --extra agent`
and set `AGENT_ENABLED=true` to expose brand profiles and collection conversations.
The OpenAI client handles chat and compiles image tasks; local task processes execute generation,
evaluation, and follow-up edits. The agent has its own database and asset storage.
Image UUIDs resolve through the shared PostgreSQL catalogue by default, with
checksum-verified files from the collection mount as sources.

The standalone factory `app.services.agent.application:create_agent_app` starts
without Qdrant or collection migrations. [Image Studio setup](../docs/image_agent_setup.md)
covers configuration and deployment; [the chat backend](../docs/chat_agent_backend.md)
documents conversation APIs, source resolution, and recovery.

## Neon collaboration

PostgreSQL stores the catalogue and ingestion ledger; Qdrant stores vectors.
The Neon CLI is local development tooling. The Docker frontend build installs only
the frontend workspace; the deployed API connects through psycopg.

Install the project-local Neon CLI and connect your checkout to the intended
project and branch:

```sh
make setup
bun run neon login
bun run neon link --project-id PROJECT_ID --branch BRANCH -y
bun run neon env pull --file .env.local --service postgres
```

Replace `PROJECT_ID` and `BRANCH` with the selected project and branch. Connection
settings and the `.neon` checkout link are ignored by Git. Each collaborator
links their own checkout. The app reads `.env`, then `.env.local`, with process
environment variables taking precedence. `DATABASE_URL` supplies the pooled
connection; `DATABASE_URL_UNPOOLED` supplies the direct migration and writer-lock
connection. A Neon pooled hostname can derive the direct hostname when omitted.

Configure `QDRANT_URL`, `QDRANT_API_KEY`, and `QDRANT_COLLECTION_NAME` in `.env`.
Use the exact collection and embedding settings associated with the ready
catalogue generation. Image delivery needs local files under `IMAGE_ROOT` or
a mounted HF bucket. Indexing needs source exports and readable image bytes.
Reuse a ready index with `make run`; there is no need to index it again.

Indexing and metadata refresh share a PostgreSQL advisory lock to prevent
concurrent writers. The local embedding cache and reports stay under
`SEARCH_DATA_DIR` (default `data/search/`). Database traces use `catalogue.*` names.
For changes that write data, use a separate Neon branch and Qdrant collection;
a database branch alone does not isolate Qdrant writes.

`neon.ts` declares an empty service policy. `bun run neon deploy` applies that
policy; FastAPI and the frontend are hosted separately from Neon.

### Mounted image storage

Use a private Hugging Face Storage Bucket mounted read-only at `/app/data/images`
and set `IMAGE_ROOT=/app/data/images` in the Space. For local development, use
`data/images/` or a read-only bind mount. Keep paths relative to this root intact,
including the extracted thumbnail directory. The catalogue stores `relative_path`
and the image checksum; it has no provider URL field.

The `/file` endpoint checks membership in the active generation, resolves the
path within `IMAGE_ROOT`, and serves bytes with `Cache-Control: private, max-age=300`.
Missing files and paths or symlinks escaping the root return 404. A catalogue that
is not ready returns 503. The browser reads images from the API's origin.

Indexing reads the same mounted files and saves relative paths in PostgreSQL and
selection snapshots. Moving unchanged files to another mount does not require
new embeddings. Set up and verify the hosted mount using the
[deployment guide](../docs/huggingface_spaces.md#image-storage).

Catalogue migration `0005` removes the retired provider URL column while keeping
IDs, relative paths, checksums, associations, and index state. Stop the previous
API before applying it; older application versions that query the removed column
cannot serve the upgraded catalogue. Downgrading restores an empty nullable column,
so restoring its old values requires a database backup.

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
uv run --all-packages --extra agent --extra web pytest
uv run ruff check .
```


## Web collection companion

`app.web:app` extends shared search with `/api/v1/explorer` routes. Install the
`web` extra and use `make web`. HF OAuth establishes account ownership;
conversations, uploads, run history and SSE endpoints enforce that ownership.
A private child process drives the pinned Python Codex SDK and six read-only MCP
tools. The search-only `app.main:app` never imports this package.

Configure credentials, origins, private state, concurrency and direct PostgreSQL
connections using [web setup](../docs/product_versions.md#run-the-web-edition).
The companion uses separate `explorer_*` tables and an Alembic version table.
It has no dependency on Studio brand profiles, Gemini or generation tasks.
