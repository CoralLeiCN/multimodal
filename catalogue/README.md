# Shared catalogue snapshot

`catalog.sqlite3.gz` is a compressed SQLite backup from 19 September 2026.
It lets collaborators use the existing Qdrant collection with their own local
image files, without repeating embedding requests.

The snapshot passed SQLite's integrity check and was verified against Qdrant
Cloud. It is about 237 KB compressed and 836 KB restored. Its active index
contains **452 images**:

| Setting | Value |
| --- | --- |
| Index version | `d7af4d4835314fe0` |
| Qdrant collection | `smg_images_d7af4d4835314fe0` |
| Embedding model | `gemini-embedding-2` |
| Embedding dimensions | `1536` |

The original catalogue pointed to an older collection that no longer exists.
This snapshot recovers the 452 images already present in the remaining cloud
collection from an interrupted 500-image run. All retained vectors and payloads
were verified before marking this snapshot ready. The other 48 selected images
had no cloud vectors and are excluded. No embeddings were generated and no
cloud data was changed during recovery.

Older runs and cached embeddings are omitted. Search uses the vectors in
Qdrant Cloud and does not need a local embedding cache. The original local
catalogue was preserved.

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
QDRANT_COLLECTION_NAME=smg_images_d7af4d4835314fe0
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
ca7bb7834fe96e27d4a02f38408e302a8bbfc3fd180a6952a2b207c9b54d1485
```
