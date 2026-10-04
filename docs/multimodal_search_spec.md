# Multimodal image search service

This specification defines the implemented search service: text and image
retrieval, metadata filtering, catalogue ingestion, and Qdrant publication. The
[local run guide](../README.md#run-locally) covers startup, and the
[deployment guide](huggingface_spaces.md) tracks the 100-image preview and hosted
acceptance. Detailed commands are in the [backend](../backend/README.md) and
[indexing](../cronjob/README.md) guides.

## User experience

The home page shows a search field, an image upload control, and a grid of indexed
images. Display the number of searchable images and identify the collection as a
sample when a sample index is active.

Support these actions:

- Search by text, such as “a brass scientific instrument” or “a steam locomotive”.
- Upload an image to find visually or semantically similar collection images.
- Select one of three image examples (Coke Cola, Modal, or Tech: Europe) from
  `examples/images` in the image search tab. Preview the selection and submit it
  with Explore through the image upload endpoint, retaining the current filters.
- Select “Find similar” on a result to search using its stored embedding.
- Filter browsing and every search by creation year range, creation place, and category.
- Open an image detail panel with its title, description, source identifiers,
  date, place, category, maker, and supplied licence and attribution information.
- Copy a supplied collection ID from image details. When the agent is enabled,
  a separate chat action closes details and opens the composer with that ID as
  its draft, preserving history without sending. Omit these actions for missing IDs.

Return 24 results by default, with a maximum of 100 per search. Browse the sample
in pages of 24 images. Use responsive cards, lazy image loading, labelled controls,
keyboard navigation, and a visible loading state. Keep the current results visible
while another query runs and ignore responses from superseded requests.
Applying changed filters clears stale results and repeats the current search, or
refreshes browsing when no search is active. Populate place and category choices
from the active catalogue. The UI selects one value per field; the API accepts
multiple values. “Clear filters” retains the current query. Invalid year ranges
disable the apply button and show a correction message.

Provide clear states for an empty index, unavailable search, invalid uploads,
missing images, and zero results. Keep model names, vector dimensions, and local
filesystem paths in operator diagnostics. The user interface should explain that
uploaded query images are processed on the server to find similar collection
images; uploaded images are not added to the collection. Status reports
`embedding_provider=local` when the index is ready. SigLIP 2 is the only supported
search model; Gemini embedding configuration is rejected.

## Technology stack and project layout

The layout follows the
[Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template),
with PostgreSQL for the catalogue and Qdrant for vectors.

| Layer | Technology | Project responsibility |
| --- | --- | --- |
| Backend | Python 3.12+, FastAPI, Uvicorn | HTTP routes, image delivery, and search orchestration. |
| Validation | Pydantic v2 and `pydantic-settings` | Typed requests, responses, and environment configuration. |
| Frontend | React, TypeScript, Vite | Search page, upload control, results grid, and image details. |
| UI and data fetching | Tailwind CSS, shadcn/ui, TanStack Router and Query | Components, navigation, and asynchronous API state. |
| API client | `@hey-api/openapi-ts` | Generate the frontend client from FastAPI's OpenAPI schema. |
| Catalogue database | Neon PostgreSQL, SQLModel, Alembic | Catalogue, ingestion status, retries, checkpoints, and schema migrations. |
| Vector database | Qdrant and `qdrant-client` | Persistent image vectors and cosine similarity queries. |
| Embeddings | SigLIP 2 Base, PyTorch, and Transformers | Local CPU text and image embeddings in a shared 768-dimensional space. |
| Tracing | Pydantic Logfire and the `logfire[fastapi]` Python SDK | Trace API requests, AI calls, ingestion, and database operations. |
| Image validation | Pillow | Decode files and validate formats and dimensions. |
| Local services | Docker Compose | Run Qdrant with persistent local storage. |
| Application container | Root Dockerfile | Build React and serve it with FastAPI on port 7860; Hugging Face deployment gaps are tracked separately. |
| Dependency management | uv and Bun | Python workspace and frontend dependencies with committed lockfiles. |
| Validation tools | pytest, Ruff, TypeScript, Biome, Playwright | Backend checks, frontend checks, and browser workflows. |

The uv workspace shares `uv.lock` and `.venv`. The `multimodal-backend` package
exports `app`; the root Python project owns data preparation dependencies. Bun
manages the frontend workspace and `bun.lock`.

| Location | Responsibility |
| --- | --- |
| `backend/app/api/` | HTTP routes and request dependencies. |
| `backend/app/core/` | Settings, database connections, tracing, and request limits. |
| `backend/app/services/` | Selection, embeddings, indexing, metadata, image delivery, and search. |
| `backend/app/alembic/` | Catalogue schema migrations. |
| `backend/tests/` | Backend and ingestion tests. |
| `frontend/src/` | Routes, components, and generated API client. |
| `cronjob/` | Data preparation and indexing entry points. |
| `scripts/` | Client generation and maintenance utilities. |
| `deploy/` | Docker preview fixtures for the HF Space. |
| `data/` | Ignored source data, images, caches, and generated reports. |

Put catalogue table models in `models.py`, Pydantic API schemas in `schemas.py`, and
settings in `core/config.py`. Keep routes thin: service modules implement
selection, embedding, ingestion, and search. The cronjob entry point calls the
same ingestion service. Generate `frontend/src/client/` from the API schema and
regenerate it whenever an endpoint contract changes.

## Architecture

The React frontend calls FastAPI. FastAPI queries Qdrant for ranked image IDs and
scores, then joins those IDs to the SQL catalogue for display metadata.
The SQL catalogue records which image version has been successfully ingested into each
Qdrant collection. The separate indexing command updates both stores.

```mermaid
flowchart LR
    D[Local images and metadata] --> I[Bounded indexing command]
    I --> G[Local SigLIP 2 model]
    G --> I
    I --> S[SQL catalogue and ingestion tracking]
    I --> Q[Qdrant vectors]
    B[React frontend] --> A[FastAPI backend]
    A --> G
    A --> S
    A --> Q
    A --> M[Indexed image endpoint]
    M --> D
    M --> H[Read-only HF bucket mount]
```

At startup, the API reads the active index version and its Qdrant collection name
from the SQL catalogue. It serves image bytes from `IMAGE_ROOT`, the HF bucket
mount in the hosted app or a local directory during development. Indexing builds a new generation independently.

The HF and web editions share that catalogue, Qdrant collection and image release.
Both use `SearchService`; Codex collection tools call the web instance of that
service. Search requests never publish vectors or create a separate agent index.
An explicitly configured `QDRANT_COLLECTION_NAME` must match the active catalogue;
otherwise status reports unavailable and vector searches return `index_mismatch`
before embedding the query. The model/revision/dimensions must also match the
published generation. Metadata browsing remains available during these
configuration errors. The [shared retrieval contract](product_versions.md#shared-retrieval-contract)
defines the deployment settings.

Use Vite during frontend development, with `/api/v1` proxied to FastAPI. Serve the
built React frontend through FastAPI for the local packaged application, following
the template. API and image routes take precedence over the frontend route fallback.
The browser accesses the SQL catalogue and Qdrant through the backend.

## Bounded data selection

Sampling requires an explicit positive `--limit` for distinct images, with a
scan limit of **1,000 source records** by default. Both `--limit` and
`--scan-limit` accept any positive integer without a fixed upper cap.
`make preview-index` supplies a 50-image limit when none is given. Plain
`make index` and the indexing script without `--limit` skip metadata scanning,
sampling, and generation creation. Reuse the saved generation matching
`QDRANT_COLLECTION_NAME`, or the sole saved generation if no collection is
configured. Return a clear error for an empty or ambiguous catalogue; use an
explicit `--limit` for the first selection and `--resume RUN_ID` to disambiguate.
Continue prepared or failed generations and verify already-ready generations.
Verified indexed images skip embedding and vector writes. Selection options,
including dry runs and preparation, require `--limit`.
Maintain a bounded sample using a stable hash of the image location and seed 42
while streaming metadata. Decode only the selected candidates after the scan.
Failed embedding requests do not cause the selection to grow.

The sampler reads bronze metadata directly and defaults to the object export.
`data/processed/image_manifest.csv` is an optional audit output.

For bronze selection, reuse the streaming record parser and metadata conventions
in [the matching script](../cronjob/match_images.py). Resolve each medium thumbnail
location against explicitly configured extraction roots, including the current
root `data/images/smg_all_medium_thumnail_images_09_04_2025/`. Test the complete
relative path under each root; multiple matches are ambiguous. Check containment
after resolving symlinks, and reject traversal or ambiguous paths.

Process configured source files in their supplied order and records in source order. Retain selected
record–image associations, including repeated references encountered before the
scan stops. Deduplicate vectors by normalized image location. This selection is a
repeatable development sample; it does not establish coverage of the collection.

Decode and validate selected images individually. Skip missing, corrupt,
unsupported, or unresolved-rights entries with a recorded reason. Use the
per-image rights fields and [the project's data guidance](../data_spec.md) when
selecting content for the intended use. Preserve supplied field values, including
empty strings and original licence spelling.

Save the selected catalogue in SQL batches of up to 500 images, with association
inserts capped at 500 rows per statement. Keep all batches in one transaction so
a preparation failure cannot publish a partial selection. Report batch progress
and confirm the commit before embedding. The embedding batch size is independent
of these catalogue batches.

Write a selection snapshot before embedding. Store it under `data/search/` with
the source paths and image checksums; keep selection counts in the catalogue.
Fetch images and their associations in batches and stream JSONL to a temporary
file, replacing the prior snapshot only on success. Resume from the saved catalogue
rows, regenerating the snapshot. Read metadata incrementally and decode at most the small
configured number of concurrent images. The full metadata and image collection
must not be loaded into memory or indexed during web application startup.

The gold Parquet remains available for later metadata enrichment. The initial
catalogue can be built from the selected bronze or manifest records, which carry
the image paths and rights fields needed by the service.

## Embedding contract

Use `google/siglip2-base-patch16-224` with PyTorch and Transformers on CPU.
The model is Apache-2.0 licensed and supports text/image retrieval.
[Model card](https://huggingface.co/google/siglip2-base-patch16-224).
Pin revision `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2`, use safetensors,
and disable remote model code. Cache downloaded files in `data/models/` by
default; serving can run offline once the cache is prepared.

Generate one 768-dimensional vector per image. Use the same model and revision
for text and uploaded-image queries. Apply EXIF orientation and convert to RGB,
then use the pinned 224-pixel processor. Pass plain query text with maximum-length
padding and truncation to 64 tokens. JPEG and PNG are the supported upload formats.
The adapter runs inference without gradients, uses two CPU threads by default,
and serializes loading and inference. Image batches are split into at most eight
images per forward pass by default. No search query or image is sent to an
external embedding API with this model.

Require one vector per input, 768 finite values, and a nonzero norm. Normalize
stored and query vectors to unit length. Use Qdrant cosine distance and descending
score; scores express similarity, not probability. Break returned equal-score
ties by image ID; candidates tied at the result limit can vary.

The configuration fingerprint includes model, revision, dimensions, and the
`siglip-rgb224-text64-v1` preprocessing contract. Query settings must match the
active generation. Any model/revision/preprocessing change requires a new index.
Use a separate Qdrant collection for a new embedding configuration and switch
query and image embeddings together. Tests inject deterministic adapters without
model downloads or external provider calls.

## SQL ingestion tracking and Qdrant storage

`DATABASE_URL` is required for the shared Neon PostgreSQL catalogue. Native
application traffic uses its pooled URL; migrations and the shared ingestion writer lock use
`DATABASE_URL_UNPOOLED` (or the direct Neon hostname derived from `DATABASE_URL`).
`CATALOGUE_TRANSPORT=postgres` is the default. HF search can use `neon_http`,
which compiles the same SQLAlchemy catalogue SELECTs into parameterized Neon
HTTPS requests on port 443. Its `DATABASE_URL` remains a PostgreSQL URL.
HTTPS runs read-only query batches, with 10-second connect and 20-second read
and pool timeouts, no redirects, and sanitized failures. Catalogue failures
produce `catalogue_unavailable` (503); status reports `unavailable`. A migrated
catalogue without a published generation remains `empty`. The web edition uses
native PostgreSQL for durable conversations and the supervisor lock.
Settings load `.env`, then `.env.local`, then process environment overrides.
PostgreSQL filters use JSONB array predicates with the same record and date
interval boundaries as Qdrant. Missing or non-PostgreSQL database configuration
fails clearly; no local catalogue is created.

Follow [Neon collaboration](../backend/README.md#neon-collaboration) for connection setup.
Indexing and metadata refresh acquire a shared PostgreSQL advisory lock on a
dedicated direct session. Explicit schema migrations serialize with a separate
transaction advisory lock.
Search API startup does not run migrations; apply them with `make migrate` before
starting or updating a deployment. The embedding cache remains local.

Keep generated files beneath `SEARCH_DATA_DIR`, defaulting to the ignored
`data/search/` directory:

```text
data/search/
  embedding_cache.sqlite3
  selections/<selection_id>.jsonl
  runs/<run_id>.json
  qdrant/
```

Neon is the authoritative catalogue and ingestion ledger. SQLite is used only
for the separate local embedding cache. Existing cache files are reused.
The repository contains no catalogue database snapshot.
Use PostgreSQL or Neon backups for the catalogue and back up Qdrant separately.
Allow one indexing writer, with network calls outside catalogue transactions.
Use Alembic migrations for PostgreSQL schema changes.

Minimum catalogue tables:

| Table | Required fields and constraints |
| --- | --- |
| `images` | Primary key `(generation_id, image_id)`; normalized source location, relative image path, image SHA-256, MIME type, decoded width and height, and JSON `filter_metadata`. |
| `image_associations` | Generation and image reference, source entry identifier, `record_uid`, `image_uid`, source JSON, title, description, original date text, JSON `places`, `categories`, and `date_ranges`, maker, catalogue identifiers, licence, copyright, and credit. |
| `image_ingestions` | Unique `(index_version, image_id)`; image checksum, embedding configuration fingerprint, Qdrant collection and point ID, status, attempt count, last error, run ID, and timestamps including `indexed_at`. |
| `index_generations` | Version, unique Qdrant collection name, model, dimension, preprocessing and query format versions, selection ID, status (`building`, `ready`, `failed`), count, and timestamps. |
| Run reports | Each generation ID is also its run ID. The catalogue holds generation status and per-image attempts; `runs/<run_id>.json` records indexed, embedded, and reused counts. |
| `service_state` | Singleton row containing the active ready index version. |

The file endpoint checks membership in the active generation and resolves
`relative_path` inside `IMAGE_ROOT`. Hosted images use a private HF bucket mounted
read-only; local development uses the same file-serving path with a local folder.
Return bytes through the API with `Cache-Control: private, max-age=300`. Missing
files, unknown IDs, and paths or symlinks outside the root return 404. An unready
catalogue returns 503. File responses never redirect to storage. Preserve relative
paths, bytes, checksums, and image IDs when copying files to the bucket.

For agent requests containing a collection record ID such as `co25823`, use the
[collection ID lookup procedure](collection_id_lookup.md). Match `record_uid`
exactly within the active ready generation and return all distinct associated
image UUIDs. This is a direct PostgreSQL workflow using the existing schema; the
text search API continues to perform semantic search.

The separate cache database contains only `embedding_cache`: a unique key derived
from the image checksum and embedding configuration fingerprint, checksum,
configuration fingerprint, validated vector JSON, and creation time. It supports
retries and reuse across generations and remains local when the catalogue is
shared or replaced. Its SQLModel metadata is separate from the catalogue schema.
Cache connections use a 30-second busy timeout.

Derive `image_id` as a UUIDv5 from a fixed project namespace and the normalized
source location. Use that UUID as the Qdrant point ID. Moving the repository or
retrying ingestion therefore preserves the ID. A shared image appears once in
results and retains its selected associations. Catalogue rows are scoped to a
generation. Separate collection builds preserve the existing generation.
Permanent collection updates modify the retained generation in place.

When `QDRANT_COLLECTION_NAME` is omitted, each generation owns a Qdrant collection
named `smg_images_<index_version>` with
one 768-dimensional dense vector per image and `Cosine` distance. Validate the
actual configured dimension and distance before writing or querying. Store
`image_id`, `image_sha256`, `embedding_config_hash`, `index_version`,
`metadata_schema_version: 1`, and `metadata` in the point payload. Keep full display
metadata and source paths in the catalogue. `metadata` mirrors `images.filter_metadata`.
[Source: Qdrant collections](https://qdrant.tech/documentation/manage-data/collections/).

Use `qdrant-client` to upsert points and `query_points` to search. The backend
joins returned IDs to `images` and `image_associations` for the active generation.
For “Find similar”, retrieve the selected point's vector and exclude its ID from
the query. A missing catalogue association or point is an index consistency error.
[Source: Qdrant query API](https://api.qdrant.tech/api-reference/search/query-points).

With `QDRANT_COLLECTION_NAME` configured, ingestion reuses the generation owning
that exact collection in the catalogue, or creates one for an empty/new collection.
Selected images and associations are upserted, earlier images are retained, and
the accumulated count and selection snapshot are updated. Embedding configuration
must match. Foreign catalogue points are rejected before upserts. Reports and
snapshots reuse the generation ID. Existing collections are never deleted or
merged automatically. `.env.example` enables this mode with `smg_images`.

Permanent updates set the retained generation to `building`, then `ready` after
verification, or `failed` on error. Resume reconciles its accumulated image rows.
The API reads active status on requests and invalidates vector verification when
completion changes. Stop the API during these updates to avoid in-flight requests,
then restart it after success. This mode has no previous-version rollback;
preparation also makes the retained active generation unavailable until resumed.
The lifecycle guarantees below about preserving the previous active index apply
only to separate collections.

## Ingestion lifecycle and recovery

Track these per-image states in the catalogue:

| State | Meaning |
| --- | --- |
| `pending` | Selected and waiting for embedding or cache lookup. |
| `embedding` | An embedding request is in progress. |
| `embedded` | A validated embedding is durably cached locally. |
| `upserting` | A Qdrant write has started; its outcome may need reconciliation. |
| `indexed` | Qdrant confirmed completion and the matching point was verified. |
| `failed` | Processing stopped with an error and retry information. |

Selection skips are counted by reason in the sampler report; only eligible
selected images receive ingestion rows.

For each selected image, check its checksum and embedding configuration. Reuse a
matching cached vector, or embed the image and commit the vector to the separate
cache database before recording `embedded` in the catalogue. These commits are
separate; retries look up the cache even if the catalogue update was interrupted.
Commit `upserting` before sending the point to Qdrant with `wait=True`. After the
write completes, retrieve the point and verify its payload and vector shape, then
commit `indexed` and `indexed_at` in the catalogue. An acknowledgement that only queues
the write is insufficient. Repeating an upsert uses the same point ID.
[Source: Qdrant upsert semantics](https://api.qdrant.tech/api-reference/points/upsert-points).

The SQL catalogue and Qdrant have separate transactions. On resume, reconcile any interrupted
`embedding` or `upserting` work. If the expected point already exists with the
matching checksum and configuration, finish the catalogue update. Otherwise retry
the upsert from the cached vector. If the interruption left no cached vector,
return the image to `pending` and generate its embedding. Verify Qdrant points
before skipping rows previously marked `indexed`; a local status alone does not
prove the point still exists after a Qdrant restore or storage loss.

Process up to 10 batches concurrently by default, configurable from 1 to 10 with
`--workers` or Make's `WORKERS`. Each batch holds up to 10 catalogue images by
default; `--batch-size` or `BATCH_SIZE` accepts 1–100. Only uncached, distinct
content is passed to the configured embedding adapter, with one output vector
per image. SigLIP serializes CPU inference and splits each batch into at most
eight images; use `WORKERS=1` for the local preview. Flush partial batches and
split requests at 12 MiB of raw image bytes. Reject individual images over that budget or `MAX_IMAGE_BYTES`. Validate
the entire response's vector count, dimensions, and values before caching it.
Commit the response's vectors together before catalogue updates or vector writes.

Use separate catalogue sessions per worker and acquire shared-checksum locks in
a stable order so concurrent batches reuse one cache entry without deadlocking.
Serialize Qdrant writes and point verification within the run. Report progress as
workers finish and preserve trace context in worker threads, including batch
attempts and per-image vector writes. Worker count and batch size can change on
resume and do not affect the embedding configuration fingerprint. Allow at most
three attempts per image across embedding and vector writes, with bounded backoff
for temporary provider or Qdrant failures. Successful parts of a split batch
remain cached; retries request only the missing content. Track attempts and
the last processing state. Stop on credential, collection configuration, or
model errors. Stop scheduling new batches on failure or interruption and wait for
active workers before closing clients, releasing the writer lock, or marking the
generation failed. New selections create new catalogue rows while reusing unchanged
image embeddings. The metadata refresh command updates filter fields on an
existing selection without re-embedding its images.

Publish only when every selected image is `indexed`, Qdrant's point count matches
the selection, and all expected IDs and payloads have been checked in bounded
batches. Mark the generation `ready` and update `service_state` in one catalogue
transaction. A failure keeps the previous active generation and its collection
available; a zero-image selection is an error. Retain generations used by running
API processes. Repair image selection or embedding changes in a new generation
using checkpointed vectors. Filter metadata can be refreshed during maintenance
with the API stopped, as described below.

At startup, validate the active collection's configuration and reconcile its
expected point IDs in bounded batches before enabling search. Report an unusable
collection as unavailable. Browse and metadata endpoints can still use the catalogue.
`--resume <run_id>` verifies ready generations and repairs interrupted generations
from the cache. Back up the catalogue database and Qdrant storage with their generation
mapping, and validate that mapping after restoration.

## Metadata filters

Store one nested `metadata` object per catalogue association and valid date
interval. Repeat that association's normalized place and category arrays for
each interval. An association with no usable date has one object with the date
keys omitted. Keep disjoint intervals separate. For example:

```json
{
  "metadata_schema_version": 1,
  "metadata": [{
    "record_uid": "co123",
    "place": ["london"],
    "category": ["optics"],
    "date_from": 1850,
    "date_to": 1870
  }]
}
```

Create these payload indexes when preparing each Qdrant collection, before
upserting vectors. Existing indexes with incompatible types are an error.

| Payload path | Qdrant type | Use |
| --- | --- | --- |
| `metadata[].place` | `keyword` | Exact normalized creation-place labels. |
| `metadata[].category` | `keyword` | Exact normalized category labels. |
| `metadata[].date_from` | `integer` | Beginning of a historical year range after date preprocessing. |
| `metadata[].date_to` | `integer` | End of that range. |

Use Unicode NFKC normalization, collapsed whitespace, and case folding for both
stored keywords and requests. Preserve source labels in the catalogue for display.
Place names are exact labels; do not infer geographic ancestry or split a
comma-separated source place into invented locations. Category names take
precedence over category values. See [the source mapping](../data_spec.md#search-filter-metadata).

`date_from` and `date_to` are inclusive integer years from -9999 to 9999; negative
years represent BCE and zero is invalid. Either request bound may be omitted.
A stored interval matches when its end is at least the requested start and its
start is at most the requested end. Thus a source range of 1850–1870 matches a
search for 1860. Unknown dates match only requests with no date restriction.
Without structured source bounds, `c.1993` maps to 1993–1993 while retaining
the original display text. See the [special date rule](../data_processing_spec.md#special-date-rule-circa-year).
Missing places and categories fail a filter on that field and otherwise remain
eligible. Reject reversed ranges, blank labels, labels longer than 300 characters,
and more than 20 values per field with `422`.

Combine fields with AND and values within each place/category field with OR.
Wrap conditions in a Qdrant nested filter so all fields match the same catalogue
association and date interval. Pass the filter into `query_points` before the
result limit is applied. Similar-image search also excludes its source point.
PostgreSQL browsing uses equivalent JSONB predicates, including the same association
boundaries. Filter options list the active generation's available labels and
year extrema; they do not change based on current filters. Catalogue browsing and
options remain usable during a Qdrant outage.
[Sources: Qdrant payload indexes](https://qdrant.tech/documentation/manage-data/indexing/),
[nested filters](https://qdrant.tech/documentation/search/filtering/).

Migration `0002` adds empty JSON metadata columns to existing catalogues. Backfill
existing selections with `cronjob/refresh_image_metadata.py --generation RUN_ID`.
It scans at most 1,000 metadata records by default, up to 10,000 with
`--scan-limit`, and requires every selected association to be found before
updating metadata. It updates the selection snapshot, catalogue metadata, and the
payloads of existing Qdrant points; vector values, embedding cache, IDs, and
ingestion statuses are preserved. Prepared runs receive an empty indexed
collection and remain pending. The command never reads or uploads image bytes.
Stop the API before refreshing a published generation and restart after success.
Retry the same command after an interruption to reconcile Qdrant with the catalogue.

## HTTP API

Use the template's `/api/v1` prefix. Define request and response contracts with
Pydantic, and expose the OpenAPI schema at `/api/v1/openapi.json` for frontend
client generation. Routes below use the frontend origin or the Vite API proxy.

| Method and route | Request and response |
| --- | --- |
| `GET /api/v1/status` | Index availability, active version, confirmed indexed image count, sample status, and search availability. |
| `GET /api/v1/filters` | Active `index_version`, `places`, `categories`, `date_min`, and `date_max`. |
| `GET /api/v1/images` | Browse with `limit`, optional `cursor`, `date_from`, `date_to`, repeated `place` and `category`; return items, `matching_images`, and the next cursor. |
| `GET /api/v1/images/{image_id}` | Selected image metadata and its source associations. |
| `GET /api/v1/images/{image_id}/file` | Bytes from the image mount for the requested active image ID. |
| `POST /api/v1/search/text` | JSON with `query`, optional `limit` and `filters` object; return ranked image results. |
| `POST /api/v1/search/image` | Multipart upload with `image`, optional `limit`, `date_from`, `date_to`, and repeated `place` and `category` fields; return ranked results. |
| `POST /api/v1/images/{image_id}/similar` | Optional JSON `limit` and `filters`; use the vector in Qdrant and exclude the selected image. |

Text request example:

```json
{
  "query": "a brass scientific instrument",
  "limit": 24,
  "filters": {
    "date_from": 1850,
    "date_to": 1900,
    "place": ["London"],
    "category": ["Optics"]
  }
}
```

Search responses contain `index_version`, `indexed_images`, `duration_ms`, and
`results`. Each result includes `image_id`, `score`, `image_url`, `title`, and
`associations` with the source identifiers, licence, copyright, and credit.
Return complete display metadata through the detail endpoint. Use the first
nonempty selected title for the card, with “Untitled image” as the fallback.

Browse results use image ID order. Cursors include the index version, last image
ID, and a hash of normalized filters. A cursor for another generation or filter
set returns `409`; the frontend starts a new page sequence when filters change.
`indexed_images` remains the total collection count and `matching_images` is the
filtered browse count. Search limits apply to distinct images, and successful queries can
return fewer items when the index is smaller than the requested limit.

Validate trimmed text between 1 and 2,000 characters and limits between 1 and 100.
Accept one JPEG or PNG upload up to 10 MiB and 20 million decoded pixels. Check
both actual format and decoded dimensions. Discard upload bytes and temporary
files after the request. Collection image validation uses the same limits.
Before parsing, bound mutation request bodies to 256 KiB for JSON and the
configured image byte limit plus 1 MiB for multipart encoding and form fields
(11 MiB by default). Count received bytes even when `Content-Length` is absent
or understated; reject oversized bodies with `413` and `body_too_large`.

Use a consistent error body with `code` and a safe, actionable `message`:

| Status | Meaning |
| --- | --- |
| `404` | Unknown image ID or an indexed file is missing. |
| `409` | Browse cursor belongs to another generation or filter set. |
| `413` | Upload exceeds the configured size or pixel limit. |
| `415` | Unsupported image format. |
| `422` | Empty query, invalid limit or metadata filters, or corrupt image. |
| `429` | Concurrent search capacity is temporarily exhausted. |
| `503` | No usable index, incompatible configuration, or unavailable embedding provider or Qdrant service. |
| `504` | Search exceeds the 30-second request deadline. |

Browsing uses the catalogue and the image mount. “Find similar” uses Qdrant and continues to
work when the embedding model is unavailable. Qdrant failure makes vector search unavailable
while browsing remains usable. Search failure must be shown as an error. Serve
files only through catalogue IDs with paths constrained to configured image roots.

## Tracing

Use [Pydantic Logfire](https://pydantic.dev/docs/logfire/get-started/) for tracing
the Python backend and AI workflows. Configure it once in each process using the
service names `multimodal-api` and `multimodal-indexer`.

Trace search requests and indexing runs, with child spans for embedding
calls, Qdrant operations, and catalogue lookups. Record duration, model, embedding
dimensions, retries, and success or failure. Include run and image identifiers
where relevant so operators can investigate slow requests and failed ingestion.
Keep credentials, request arguments, query-string values, headers, image bytes,
embedding vectors, and raw exception messages out of traces. Record error types
and safe application error codes to identify failures.

The backend declares `logfire[fastapi]`. The API loads `LOGFIRE_TOKEN` from backend
settings or uses the ignored `.logfire/` project credentials. Indexing loads
`LOGFIRE_INDEXER_TOKEN` or uses `.logfire/indexer/` credentials for its own project.
Shared SDK destination variables cannot override the indexing project, and
indexing does not fall back to API credentials. `LOGFIRE_SEND_TO_LOGFIRE`
defaults to `if-token-present`; `false` disables cloud export.
`LOGFIRE_ENVIRONMENT` defaults to `development`. Flush pending telemetry on API
shutdown and indexing exit. Python tests and OpenAPI export disable cloud export.
Validate that child spans separate provider latency from database latency and
that captured telemetry excludes request content and raw provider errors. See
[the tracing setup guide](../backend/README.md#logfire-tracing).

## Configuration and operation

SigLIP 2 search needs no API key. Configure its model cache, pinned revision,
CPU threads, and inference batch size in the backend environment. Download the
weights before enabling `HF_HUB_OFFLINE=1`.
Store the resulting vectors in local Qdrant or Qdrant Cloud and the catalogue and ingestion
ledger in the SQL catalogue. Keep credentials in backend settings; frontend environment
variables contain only public configuration such as the API base URL.

Configuration defaults:

| Setting | Default |
| --- | --- |
| Embedding model | `google/siglip2-base-patch16-224` |
| Embedding revision | `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2` |
| Embedding dimensions | `768` |
| `EMBEDDING_MODEL_CACHE` | `data/models/` |
| `EMBEDDING_CPU_THREADS` | `2` |
| `EMBEDDING_BATCH_SIZE` | `8` |
| Selected image limit | `50` |
| Source scan limit | `1000` |
| Search data directory | `data/search/` |
| `DATABASE_URL` | Required PostgreSQL catalogue URL |
| `DATABASE_URL_UNPOOLED` | Optional direct URL for migrations and writer locks |
| `CATALOGUE_TRANSPORT` | `postgres`; set `neon_http` for HF catalogue reads over HTTPS 443 |
| `SEARCH_DATA_DIR` | `data/search/`, resolved from the repository root; local cache and reports |
| `QDRANT_URL` | `http://127.0.0.1:6333` for processes on the host |
| `QDRANT_COLLECTION_PREFIX` | `smg_images` |
| `QDRANT_API_KEY` | Optional backend secret when the Qdrant instance requires authentication |
| Default result count | `24` |
| API bind address | `127.0.0.1:8000` locally; `0.0.0.0:7860` in the application container |
| Frontend development address | `http://localhost:5173` |

The `qdrant` service in `compose.yml` uses a pinned image and persists
`/qdrant/storage` through a bind mount at `data/search/qdrant/`. Its HTTP port
is published as `127.0.0.1:6333:6333`. Containerized backend and indexing processes
use `http://qdrant:6333`; host processes use `QDRANT_URL` above. Configure the same Neon catalogue in both Python processes and resolve image
roots consistently. Keep each indexing machine’s cache under `SEARCH_DATA_DIR`.
[Source: Qdrant local setup](https://qdrant.tech/documentation/quickstart/).

The following commands run from the repository root. The Compose configuration
currently runs Qdrant; the Python backend and Vite development server run on the host.

The root Dockerfile serves the compiled frontend and API on port 7860. The
[deployment guide](huggingface_spaces.md) contains the local Docker setup,
release scope, database connection checks, and remaining hosted work.

```sh
uv sync --locked --all-packages
bun install --frozen-lockfile
docker compose up -d qdrant

# Apply the catalogue schema migrations.
uv run --package multimodal-backend --env-file .env alembic -c backend/alembic.ini upgrade head

# Preview a small selection and estimated request count without loading the embedding model.
uv run --package multimodal-backend cronjob/index_images.py --limit 20 --scan-limit 1000 --dry-run

# Build the sample and reuse cached vectors on subsequent runs.
uv run --package multimodal-backend --env-file .env cronjob/index_images.py --limit 50 --scan-limit 1000

# Start the backend with the published index.
uv run --package multimodal-backend --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000

# Start the React development server in another terminal.
bun run dev

# Regenerate the frontend client after API schema changes.
bash scripts/generate-client.sh
```

Support repeatable `--metadata`, `--seed`, `--limit`, `--scan-limit`, `--dry-run`,
`--prepare-only`, and `--resume <run_id>`. Omission of `--limit` reuses a saved
selection, as described above. Configure roots and storage paths through
backend settings. Reject conflicting selection options when resuming. Exit `0`
for success and `2` for invalid options or a failed run. Report partial selections
clearly. Dry runs and preparation make zero embedding calls or Qdrant writes.

## Validation

Acceptance checks:

- The selection stops at the configured image or scan limit and preserves source
  identifiers, associations, rights fields, and empty metadata.
- A restart reuses the published index; an unchanged indexing run reuses cached
  embeddings. An interrupted or failed run preserves the previous index.
- The catalogue records `indexed` only after a confirmed Qdrant write. Tests cover a
  crash between the Qdrant write and catalogue update, repeated upserts, missing
  points after a restore, and model or dimension mismatches.
- Tests cover missing and ambiguous paths, corrupt files, symlink escapes,
  duplicate image references, invalid vectors, provider failures, and CLI codes.
- Qdrant integration tests use known vectors to verify cosine ordering, ordering
  of returned ties, limits, exclusion of the source image, and catalogue metadata
  joins. Use exact Qdrant search for these small deterministic fixtures.
- API tests cover upload limits, missing indexes, pagination, and error responses.
  The frontend loads only displayed images and handles stale search responses.
- Metadata tests cover inclusive range overlap, missing values, disjoint ranges,
  multiple associations, normalization, OR within fields, AND across fields,
  filtering before top-k selection, cursor/filter consistency, and payload-only
  refresh. An opt-in server test verifies the four real Qdrant payload indexes.
- A small, fixed set of queries has expected relevant images identified within
  the sample. Record recall at 10 and inspect the results before increasing the
  indexed collection; distinguish absent sample content from retrieval failures.
- Measure Qdrant search and catalogue lookup separately from provider latency.
  Aim for under 200 ms at the 95th percentile for the default sample on the
  development machine, recording hardware and sample size with the measurements.
- Pytest covers `backend/tests/` and `cronjob/`.
  Run affected pytest tests and Ruff for Python changes. Run TypeScript and Biome
  checks and exercise affected React workflows with Playwright and in a browser.

Database tests require `TEST_POSTGRES_URL` for a disposable PostgreSQL database
using a direct connection. Tests create and remove isolated schemas; they never
fall back to the application's `DATABASE_URL`. Local cache tests use temporary
SQLite files.
