# Set up the shared catalogue

Neon holds the catalogue and indexing status; Qdrant holds the search vectors.
You need access to both services and either R2 image credentials or local thumbnails.
Install Python 3.12+, uv, Bun, and Make before starting.

## 1. Install and connect

Run from the project root:

```sh
make setup
bun run neon login
bun run neon link --project-id orange-smoke-07633598 --branch production -y
bun run neon env pull --file .env.local --service postgres
```

`make setup` installs the Neon CLI. The connection commands save the database
settings locally in ignored files.

## 2. Configure the app

Fill in these values in `.env`:

```dotenv
GEMINI_API_KEY=your-gemini-key
QDRANT_URL=your-shared-cluster-url
QDRANT_API_KEY=your-qdrant-key
```

Keep the collection name, embedding model, and dimensions from `.env.example`
to match the shared index. To load the uploaded images from R2, add:

```dotenv
R2_ENDPOINT_URL=https://your-account-id.r2.cloudflarestorage.com
R2_BUCKET=smg-images
R2_ACCESS_KEY_ID=your-access-key-id
R2_SECRET_ACCESS_KEY=your-secret-access-key
R2_PREFIX=
```

Use a bucket-scoped Object Read token. Stored `images.r2_url` rows
load through temporary signed URLs, so browsing needs no local image folder.
Indexing automatically saves R2 links using the completed upload's path rule,
without checking R2. See [R2 catalogue links](../backend/README.md#r2-catalogue-links). For local-only delivery, leave `R2_ENDPOINT_URL` empty and put thumbnails
in `data/images/`, preserving their folders, or set `IMAGE_ROOT` to their location.
Indexing still reads local images. Set `IMAGE_ROOT` to the parent `data/images/`
directory so relative paths retain the extracted thumbnail folder.

## 3. Run

```sh
make run
```

Open [localhost:8000](http://localhost:8000). The app uses the shared Neon
catalogue and existing Qdrant index. SQLite is used only for the local embedding
cache under `data/search/`; `SEARCH_DATA_DIR` changes that folder.

For imports, indexing, backups, and PostgreSQL tests, see
[Neon maintenance](../backend/README.md#neon-collaboration).

## Missing migration 0004 at startup

If startup reports `Can't locate revision identified by '0004'`, the database has
advanced beyond the migration files in the checkout. Revision `0004` records the
nullable `images.r2_url` string column for object-storage URLs. Keep
`backend/app/alembic/versions/0004_r2_image_urls.py` with the application code.
An already upgraded database requires no schema change when this file is restored.
Do not reset the revision marker or delete catalogue data to bypass the error.
