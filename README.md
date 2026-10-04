---
title: Collection Explorer
emoji: 🔎
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
---

# Collection Explorer

Search the Science Museum Group collection with text, uploaded images, or the
“Find similar” action on a result. Browse indexed images, filter by creation year,
place, and category, and view source identifiers, licences, and attribution.

React and TypeScript provide the frontend; FastAPI serves the API and compiled
frontend. SigLIP 2 Base runs on CPU to embed text and images. PostgreSQL stores the
catalogue and ingestion state, and Qdrant stores vectors. Local SQLite caches
embeddings between indexing runs. Search requires no model API key and has no
daily quota.
Search execution is limited to 30 seconds and four concurrent jobs per API.
Initial index verification streams catalogue rows and vector batches in a shared
background job. Browsing is available while verification runs, and search timeouts
do not restart its progress.

The first release is a public Hugging Face Docker Space for dataset exploration.
It includes only search features. A separate web edition adds
Codex for agentic collection search, using the Python SDK over app server.
`make run` builds search; `make web` builds the collection companion with HF
account login. Both are implemented locally; live web credentials and hosted
validation remain pending.
The [version and harness decision](docs/product_versions.md) records the shared
architecture, current gaps and implementation order.
Both editions and Codex collection tools share one published Qdrant collection,
its PostgreSQL catalogue and the matching HF image release. The
[shared retrieval contract](docs/product_versions.md#shared-retrieval-contract)
records the settings and validation rules; index the dataset once for both apps.
The 100-image local preview has passed acceptance; hosted deployment is pending.
The [deployment guide](docs/huggingface_spaces.md) tracks the remaining work and
provides a reproducible preview with local PostgreSQL, Qdrant, and the app on
port 7860.

A later release will publish the collection datasets on Hugging Face. The
[publication plan](data_spec.md#planned-hugging-face-dataset-publication) covers
release contents, source identifiers, and attribution.

## Run locally

Install Python 3.12+, uv, Bun, Make, and Docker with Compose. Run commands from
the repository root:

```sh
make setup
```

Setup installs locked dependencies and creates `.env` if absent. Configure
`DATABASE_URL` for PostgreSQL and the Qdrant connection in `.env`. For a shared
Neon catalogue, follow [Neon collaboration](backend/README.md#neon-collaboration).
Settings load `.env`, then `.env.local`; process environment variables take
precedence. An existing index must match the configured collection name, model,
revision, dimensions, and preprocessing.
Run `make migrate` before serving after a schema update; search startup performs
no migrations. The HF edition can read Neon over HTTPS with
`CATALOGUE_TRANSPORT=neon_http`. Local and web editions use `postgres` by default.

For a new index, place the object JSON export under `data/bronze/` and extracted
thumbnails under `data/images/`, or configure `IMAGE_ROOT` and pass a metadata
path. Setup does not download collection data. See [data sources and paths](data_spec.md).
For local Qdrant, set `QDRANT_URL=http://127.0.0.1:6333` and leave
`QDRANT_API_KEY` empty, then run:

```sh
make qdrant-up
make preview-index
make index LIMIT=50 WORKERS=1 BATCH_SIZE=8
make run
```

For Qdrant Cloud, configure `QDRANT_URL=https://your-cluster-host:443` and
`QDRANT_API_KEY`, and skip `make qdrant-up`. Reusing an existing ready index needs
only `make run`. Open [localhost:8000](http://127.0.0.1:8000); API documentation is
at [localhost:8000/docs](http://127.0.0.1:8000/docs). The command builds the frontend
and serves it through FastAPI. Press Ctrl+C to stop, or use `PORT=8001` to choose
another port.

The default model is `google/siglip2-base-patch16-224` with 768 dimensions and a
pinned revision. Its first embedding operation downloads about 1.5 GB of weights
to `data/models/`; later runs reuse them. Use `HF_HUB_OFFLINE=1` after preparing
the cache. Short descriptions work best because the model accepts 64 text tokens.
Model or preprocessing changes require a separate index and an API restart.

`make preview-index` selects up to 50 images from 1,000 records without embedding.
Plain `make index` resumes the saved selection and skips verified indexed images.
Use `LIMIT` and `SCAN_LIMIT` to select a new sample, or
`INDEX_ARGS="--resume RUN_ID"` to choose a saved run. For CPU inference, use
`WORKERS=1 BATCH_SIZE=8`. Stop the API while updating a permanent collection and
restart after publication. Full selection, recovery, and maintenance commands
are in the [indexing guide](cronjob/README.md#sample-and-index-images).

Collection images use a private Hugging Face Storage Bucket mounted read-only at
`IMAGE_ROOT`. Local development uses `data/images/` or the preview's bind mount.
Keep catalogue relative paths unchanged when uploading; indexing reads those
same files. See [mounted image storage](backend/README.md#mounted-image-storage).

## Development and checks

Run `make backend` and `make dev` in separate terminals, then open
[localhost:5173](http://127.0.0.1:5173). `make qdrant-down` stops local Qdrant while
preserving its data. The Makefile also finds Bun in
`data/tools/node_modules/.bin` when installed there.

Set `TEST_POSTGRES_URL` to a disposable PostgreSQL database's direct URL, then run
`make check` for Python tests, lint checks, and the frontend build. Run browser
checks against the built application as described in the
[frontend guide](frontend/README.md). `make help` lists available commands.

Use the [backend guide](backend/README.md) for API contracts and tracing, and the
[search specification](docs/multimodal_search_spec.md) for indexing and retrieval
rules. Exact collection IDs such as `co25823` use the parameterized PostgreSQL
[collection ID lookup](docs/collection_id_lookup.md).

## Collection companion and Image Studio

`make studio` runs the optional brand image-generation prototype separately.
With `AGENT_ENABLED=true`, its sidebar supports brand conversations,
image generation, and follow-up edits. `/create` manages six brand design fields.
The OpenAI client handles chat through a configured compatible endpoint; local
task processes execute image tasks, and a separate private HF bucket
keeps generated assets separate from the collection index.

[Image Studio setup](docs/image_agent_setup.md) covers credentials, cloud services,
and validation. [The chat backend guide](docs/chat_agent_backend.md) documents
conversation APIs and recovery, with a [Chinese version](docs/chat_agent_backend.CN.md).
Designers can edit [brand prompts](docs/brand_prompts.md). `make agent-api` starts
the standalone agent API; `make agent-worker` processes its queue. The web companion uses the separate
`web` dependency extra and `EXPLORER_` settings described in the
[web setup](docs/product_versions.md#run-the-web-edition).

## Collection data

Sources: [SMG datasets](https://coimages.sciencemuseumgroup.org.uk/datasets/index.html)
and [SMG Collection](https://collection.sciencemuseumgroup.org.uk/).
The project uses the object and document exports named `with_CC_images` and the
medium-thumbnail archive. Reuse depends on each image's specific Creative Commons
licence, including attribution and any NonCommercial, ShareAlike, or NoDerivatives
conditions. Consult [data and licensing notes](data_spec.md) before reuse.

| Layer | Location | Contents |
| --- | --- | --- |
| Bronze | `data/bronze/` | Original object and document JSON exports. |
| Silver | `data/silver/` | Official SMG processed object CSV. |
| Gold | `data/gold/` | Silver rows with available local images, stored as Parquet. |
| Images | `data/images/` | Extracted thumbnails with their original relative paths. |

Generate `data/gold/object_records.parquet` with
`uv run cronjob/silver_to_gold.py`. The converter preserves columns, text values,
and empty fields for rows whose image paths match local files. Run
`python3 cronjob/match_images.py` for an optional manifest and coverage audit under
`data/processed/`. The [pipeline guide](cronjob/README.md) documents options and
output fields; [data processing rules](data_processing_spec.md) define date handling.
Generated data is ignored by Git.

Attribution: © The Board of Trustees of the Science Museum.
Source: [Science Museum Group Collection](https://collection.sciencemuseumgroup.org.uk/).
