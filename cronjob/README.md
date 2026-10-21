# Application indexing commands

SMG source processing lives in `smg-datasets`. This directory contains application
indexing and metadata synchronization commands that consume the published
Hugging Face `catalogue` configuration. See [the consumption contract](../data_processing_spec.md).

Configure `HF_DATASET_ID=YOUR_ACCOUNT/YOUR_DATASET` and set
`HF_DATASET_REVISION` to the full commit SHA returned by the publishing script.
The repository ID is a placeholder until supplied. For offline review before
publication, use `HF_DATASET_PATH=/absolute/path/to/prepared-package` instead.
Configure PostgreSQL, Qdrant and `IMAGE_ROOT=data/hf-images` as usual.

```sh
uv sync --locked --all-packages
make migrate
make qdrant-up
make preview-index LIMIT=50 SCAN_LIMIT=1000
make index LIMIT=50 SCAN_LIMIT=1000 WORKERS=1 BATCH_SIZE=8
make run
```

Preview verifies the prepared release and caches selected original image bytes.
It performs no database, Qdrant or embedding writes. `SCAN_LIMIT` counts prepared
image rows, and `LIMIT` bounds the number selected. All source associations for
a selected image come from the prepared row. Sampling is stable by source image
location and `--seed` (default 42). Source parsing, matching, rights selection,
date interpretation and label normalization happen in `smg-datasets`.

`--prepare-only` saves catalogue rows and a selection snapshot without embeddings.
The catalogue uses batches of up to 500 rows within one transaction; snapshots
are streamed to a temporary file and replaced atomically. Embedding batches
remain independently configurable with `BATCH_SIZE` and `WORKERS`.

```sh
uv run --package multimodal-backend cronjob/index_images.py --limit 50 --prepare-only
make index
uv run --package multimodal-backend cronjob/index_images.py --resume RUN_ID --workers 1 --batch-size 8
```

Plain indexing resumes the configured collection or sole saved generation. It
skips dataset downloading and selection, and reuses verified vectors and cached
embeddings. Multiple generations require an explicit `--resume` or configured
collection. An explicit `--limit` selects again. Selection flags require `--limit`;
`--metadata` has been removed.

The dataset repository and revision are recorded on each generation. A permanent
collection refuses another release to prevent mixing metadata. Use a new
collection for a new image selection, or explicitly refresh all existing images'
metadata when their identities and checksums are unchanged:

```sh
uv run --package multimodal-backend cronjob/refresh_image_metadata.py \
  --generation RUN_ID --scan-limit 200000
```

Refresh reads prepared metadata, requires every selected image, preserves vectors
and ingestion state, and updates PostgreSQL and Qdrant payloads. The scan limit
defaults to 1,000 prepared image rows. Stop serving a permanent collection during
index updates and restart after success. Retrying repairs an interrupted payload
update. Both writers retain the shared PostgreSQL advisory lock.

The collection image cache must be available at the same relative paths to both
application editions. A read-only mounted copy is suitable for serving. No raw
SMG exports or extracted source image archive are required in this repository.

When resuming a pre-migration generation, retain its original `IMAGE_ROOT`
so existing image paths still resolve. New HF selections add content-addressed
files beneath that root. Run `make migrate` to apply migration `0006` first.
