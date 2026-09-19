# Set up the shared catalogue

Neon holds the catalogue and indexing status; Qdrant holds the search vectors.
You need access to both services and a copy of the collection thumbnails.
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
to match the shared index. Put the thumbnails in `data/images/`, preserving their
folders, or set `IMAGE_ROOT` in `.env` to their location.

## 3. Run

```sh
make run
```

Open [localhost:8000](http://localhost:8000). The app uses the shared Neon
catalogue and existing Qdrant index. SQLite is used only for the local embedding
cache under `data/search/`; `SEARCH_DATA_DIR` changes that folder.

For imports, indexing, backups, and PostgreSQL tests, see
[Neon maintenance](../backend/README.md#neon-collaboration).
