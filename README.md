# Multimodal

This project aims to build multimodal search for the Science Museum Group collection, enabling users to explore the collection using text and images.

See [the user features](docs/user_features.md) for what we want to build.

## Run locally

To reuse the shared 452-image Qdrant Cloud index, follow the
[catalogue restore guide](catalogue/README.md). Download its SQLite snapshot
with Git LFS and restore it before starting the app. Local images and access
to the matching cloud collection are required. Ingestion keeps cached embeddings
in the separate, ignored `data/search/embedding_cache.sqlite3` file. The catalogue
can be exported and shared as a complete database.

Run these commands from the project root. Install Python 3.12+, uv, Bun, Make,
and Docker with Compose first, and start Docker. Make commands also find the
workspace's local Bun installation in `data/tools/node_modules/.bin` when present.

```sh
make setup
# Edit .env and set GEMINI_API_KEY; setup preserves an existing .env.
# For local Qdrant, set QDRANT_URL=http://127.0.0.1:6333 and QDRANT_API_KEY=
make qdrant-up
make preview-index
make index
make run
```

For Qdrant Cloud, fill in `QDRANT_URL` and `QDRANT_API_KEY` in `.env` with your
cluster endpoint and key, and skip `make qdrant-up`. The example configuration
uses Cloud placeholders and 1,536 embedding dimensions. If an existing `.env`
uses 768 dimensions, change `EMBEDDING_DIMENSIONS` to `1536`, build a new index
with `make index`, and restart the app.

Indexing requires the object JSON export under `data/bronze/` and extracted
thumbnails under `data/images/`; see [data sources and paths](data_spec.md).
Setup installs dependencies and creates configuration; it does not download
collection data. The preview selects up to 50 images from 1,000 records without
embedding requests. `make index` sends selected images to Google's Gemini API
and may incur API charges. Text and uploaded-image searches also use Gemini.

Open `http://127.0.0.1:8000` after `make run` starts. This command builds the
frontend and serves it through the backend in the foreground; press Ctrl+C to
stop it. API documentation is at `http://127.0.0.1:8000/docs`.
If port 8000 is occupied, use `make run PORT=8001` and open
`http://127.0.0.1:8001`.
On later launches, use `make run` to reuse the existing cloud index. For local
Qdrant, also run `make qdrant-up` first.
Restart the app after publishing a replacement index.

Set `QDRANT_COLLECTION_NAME` in `.env` to reuse one collection across runs.
The example uses `smg_images`; to retain an existing collection, use its exact
name with its original SQLite catalogue. Ingestion adds or updates selected
images and retains earlier images. Stop the API during these updates and restart
it after completion. See [permanent collection setup](cronjob/README.md#permanent-collection).

For development, run `make backend` in one terminal and `make dev` in another,
then open `http://127.0.0.1:5173`. Qdrant and an index are still needed for vector
search. `make qdrant-down` stops the database and keeps its data.

Use `make help` to list targets and `make check` for Python tests, lint checks,
and the frontend build. Change the sample with
`make preview-index LIMIT=20 SCAN_LIMIT=1000` and then the same options on
`make index`. `INDEX_ARGS` passes extra selection options such as `--seed 7` or
`--metadata path/to/export.json`; use the direct
[indexing command](cronjob/README.md#sample-and-index-images) to resume a run.

## Collection Explorer

The prototype has a React frontend and dedicated FastAPI search endpoints.
SQLite tracks image ingestion; Qdrant stores the multimodal vectors. Start with
a sample of 50 local images using the
[backend setup guide](backend/README.md), then build or run the
[frontend](frontend/README.md). The application is served at
`http://127.0.0.1:8000`, with API documentation at `/docs`.
Creation year, creation place, and category filters apply to browsing, text,
image, and similar-image searches. Qdrant indexes these metadata fields and
SQLite retains their source labels and date ranges.
Date preprocessing follows the [data processing rules](data_processing_spec.md),
including treating `c.1993` as 1993 for filtering while preserving its display text.

Pydantic Logfire traces API requests, Gemini embedding calls, indexing, and
database operations. Cloud export uses separate local project credentials for
the API and indexing, or `LOGFIRE_TOKEN` and `LOGFIRE_INDEXER_TOKEN` in `.env`.
See [tracing setup](backend/README.md#logfire-tracing).

The indexing command reads a bounded portion of the bronze metadata and sends
only selected image bytes to Gemini. It saves checkpoints so completed embeddings
can be reused. See [the indexing commands](cronjob/README.md#sample-and-index-images)
and [the service specification](docs/multimodal_search_spec.md).

## Data sources

This project uses data from the Science Museum Group:

- [Science Museum Group Datasets](https://coimages.sciencemuseumgroup.org.uk/datasets/index.html)
- [Science Museum Group Collection](https://collection.sciencemuseumgroup.org.uk/)

We downloaded the object and document exports named `with_CC_images` and the
supplied medium-thumbnail image archive. **CC means Creative Commons**: reuse
depends on each image's specific licence, including attribution and any
NonCommercial, ShareAlike, or NoDerivatives conditions.

See [the data and licensing notes](data_spec.md) for the licence meanings,
requirements, handling verified in this project, and outstanding licence checks.

Attribution: © The Board of Trustees of the Science Museum.
Source: [Science Museum Group Collection](https://collection.sciencemuseumgroup.org.uk/).

## Data layers

| Layer | Location | Contents |
| --- | --- | --- |
| Bronze | `data/bronze/` | Original object and document JSON exports for reference. |
| Silver | `data/silver/` | Official SMG processed object CSV. |
| Gold | `data/gold/` | Silver records whose referenced images are available locally, stored as Parquet. |

Generate the gold file with:

```sh
uv sync --locked
uv run cronjob/silver_to_gold.py
```

The converter uses pandas for CSV and Parquet operations, with PyArrow as the
Parquet engine. It keeps rows whose `image` path matches a local file under
`data/images/` and reports how many rows were removed. All columns and their text
values are retained for those rows, including empty cells, identifiers, and
historical date ranges. The output is written to `data/gold/object_records.parquet`.

See [the pipeline documentation](cronjob/README.md) for options.

## Optional image metadata audit

Run `python3 cronjob/match_images.py` when an image/metadata manifest or coverage
audit is needed. It creates `data/processed/` from the bronze exports and local
images. These generated outputs can be removed after review and rebuilt on demand.
The extracted thumbnails live in `data/images/`.
See [the matching script documentation](cronjob/README.md)
for output fields and options.
