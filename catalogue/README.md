# Shared catalogue snapshot

`catalog.sqlite3.gz` is a compressed SQLite backup from 19 September 2026,
stored in Git LFS.
It lets collaborators use the existing Qdrant collection with their own local
image files, without repeating embedding requests.

The snapshot passed SQLite's integrity check and was verified against Qdrant
Cloud. It is about 236 KB compressed and 827 KB restored. Its active index
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

Older runs are omitted from this snapshot. Its schema is at migration `0003`
and contains no embedding cache table. Local ingestion populates and reuses
`data/search/embedding_cache.sqlite3`, a separate file ignored by Git. Search
uses the vectors in Qdrant Cloud. The original local catalogue was preserved.

## Restore

Install [Git LFS](https://git-lfs.com/) and run these commands from the repository
root, then install dependencies with `make setup`:

```sh
git lfs install --local
git lfs pull --include="catalogue/catalog.sqlite3.gz"
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

If browsing works but search reports that Qdrant is unavailable, check that the
active SQLite catalogue belongs to a collection that still exists in your
configured cluster. `QDRANT_COLLECTION_NAME` selects the collection for ingestion;
changing it does not switch the active catalogue used by search. To replace an
older catalogue with this snapshot, stop the backend and ingestion, back up the
existing catalogue with SQLite's backup API, remove the old database only after
checking the backup, and run the restore command above. Keep the separate
embedding cache. Restart the backend and check `/api/v1/status` for `ready` and
452 indexed images.

Place the extracted thumbnails under `data/images/`, preserving the directory
layout described in [the data specification](../data_spec.md), then run
`make run`. Skip `make index` when reusing this snapshot. Coordinate subsequent
index changes with collaborators: their catalogue copies must match the cloud
collection. Use a separate collection for independent indexing experiments.

The snapshot includes collection metadata and attribution. Its content remains
subject to the source terms documented in [the data specification](../data_spec.md).

SHA-256 of the restored SQLite file:

```text
86e105787bb44e403ff108b78ee910c9d3dfefbaf2d725b3d4893bd3ad0b398c
```

## Update the shared snapshot

Stop ingestion and metadata updates while exporting, so the catalogue matches
the Qdrant collection. Use the original database for the collection you plan to
share. The command defaults to `data/search/catalog.sqlite3`; pass `--source`
when `SQLITE_PATH` points elsewhere:

```sh
python3 scripts/export_catalogue.py
git add catalogue/catalog.sqlite3.gz
git commit -m "update shared catalogue snapshot"
git push
```

The exporter uses SQLite's backup API to copy the complete catalogue. It checks
database integrity and foreign keys before replacing the compressed snapshot.
The embedding cache is a separate local file, so exports need no row filtering.
Git LFS tracks only the compressed catalogue snapshot. Restoring the catalogue
preserves any existing local cache file.

For a catalogue created before migration `0003`, stop app and ingestion processes
and run this one-time upgrade before exporting:

```sh
uv run --package multimodal-backend alembic -c backend/alembic.ini upgrade head
```

The migration copies cached vectors into `embedding_cache.sqlite3` beside the
catalogue, then removes the old table and compacts the catalogue. An interrupted
upgrade can be retried. The exporter rejects older catalogues containing the
cache table, as well as attempts to export the cache database itself.

Update this guide's image count, collection settings, and restored SHA-256 when
replacing the snapshot. Collaborators need the corresponding Qdrant collection
and image files; those assets are supplied separately.
