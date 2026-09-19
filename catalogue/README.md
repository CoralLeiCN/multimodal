# Legacy catalogue archive

The app requires Neon for its catalogue. Follow [setup](../docs/neon_setup.md)
to connect. This archive is retained for historical recovery and one-time imports.

`catalog.sqlite3.gz` is a compressed SQLite backup from 19 September 2026,
stored in Git LFS.
It records an older index state; the live Qdrant collection may have grown since
this snapshot. Recovery requires a Qdrant backup with matching membership.

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

## Recover the archive for an import

Download the archive with Git LFS and extract it to a separate file:

```sh
git lfs pull --include="catalogue/catalog.sqlite3.gz"
python3 scripts/restore_catalogue.py --output data/legacy/catalog.sqlite3
```

The restore refuses to overwrite an existing file. Its output is an import
source, not an app database. Follow the
[Neon import instructions](../backend/README.md#import-an-existing-sqlite-catalogue)
with an empty destination and matching Qdrant collection. Source identifiers and
image attribution remain subject to [the data terms](../data_spec.md).

SHA-256 of the restored file:

```text
86e105787bb44e403ff108b78ee910c9d3dfefbaf2d725b3d4893bd3ad0b398c
```

`scripts/export_catalogue.py --source PATH --output PATH` supports legacy SQLite
archives only. It checks integrity and foreign keys and rejects cache databases.
Current catalogue migrations accept PostgreSQL only; upgrade older SQLite
schemas using the earlier project version before attempting an import.
