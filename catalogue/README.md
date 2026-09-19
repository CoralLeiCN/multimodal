# Shared catalogue snapshot

`catalog.sqlite3.gz` is a compressed SQLite backup from 19 September 2026.
It lets collaborators use the existing Qdrant collection with their own local
image files, without repeating embedding requests.

The backup passed SQLite's integrity check. It is about 7.5 MB compressed and
18.8 MB restored. Its active index contains **50 images**:

| Setting | Value |
| --- | --- |
| Index version | `16153c4c64da4e49` |
| Qdrant collection | `smg_images_16153c4c64da4e49` |
| Embedding model | `gemini-embedding-2` |
| Embedding dimensions | `1536` |

The snapshot also retains older indexing history, two failed 500-image runs,
and cached embeddings. Those failed runs are not the active collection.
The matching cloud collection's availability was not checked during export.

## Restore

From the repository root, install dependencies with `make setup`, then run:

```sh
python3 scripts/restore_catalogue.py
```

This creates `data/search/catalog.sqlite3` and refuses to overwrite an existing
file. If `SQLITE_PATH` in your `.env` uses a different location, pass that path
with `--output`. Restore before starting the backend; startup creates an empty
database if one does not exist.

Set these values in your own `.env`:

```dotenv
QDRANT_COLLECTION_NAME=smg_images_16153c4c64da4e49
EMBEDDING_MODEL=gemini-embedding-2
EMBEDDING_DIMENSIONS=1536
SQLITE_PATH=data/search/catalog.sqlite3
```

Also set `QDRANT_URL` and `QDRANT_API_KEY` for the cluster containing this
collection. Your own unrelated Qdrant cluster will not contain its vectors.
Set your own `GEMINI_API_KEY` for text and uploaded-image queries.
Credentials are not included in this snapshot.

Place the extracted thumbnails under `data/images/`, preserving the directory
layout described in [the data specification](../data_spec.md), then run
`make run`. Skip `make index` when reusing this snapshot. Coordinate subsequent
index changes with collaborators: their catalogue copies must match the cloud
collection. Use a separate collection for independent indexing experiments.

The snapshot includes collection metadata and attribution. Its content remains
subject to the source terms documented in [the data specification](../data_spec.md).

SHA-256 of the restored SQLite file:

```text
0955e029b48641d2441284a7cfd11a6922fba60e42200446467ef19301216c9f
```
