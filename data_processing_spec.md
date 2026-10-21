# Prepared dataset consumption

Dataset preparation is owned by `smg-datasets`. It parses original SMG exports,
normalizes search metadata, matches images, applies the release's rights policy,
validates the result and publishes the Hugging Face dataset. The source schema
proposal and field decisions also live in that repository.

This application consumes package schema `3.0.0`, configuration `catalogue`,
contract version `1.0.0`. It performs sampling, byte caching, embeddings,
PostgreSQL catalogue loading and Qdrant indexing. No source JSON or CSV is read.

## Release selection

Set `HF_DATASET_ID` to the eventual `owner/dataset` and `HF_DATASET_REVISION` to
its full 40-character commit SHA. Branch names are rejected. For local offline
review, `HF_DATASET_PATH` can point to an already-built package instead; its
manifest hash becomes the release identity. The repository ID remains a
placeholder until supplied by the owner.

The consumer downloads the release manifest and catalogue Parquet shards,
verifies shard hashes and sizes, then loads the prepared rows through Hugging
Face Datasets. It validates the catalogue contract version and caches original
selected image bytes under `IMAGE_ROOT/hf/<sha256>.<extension>`. Original source
locations and UUID5 image identities remain in the catalogue. Cache paths are
contained beneath `IMAGE_ROOT`, and cached bytes must match their checksum.

## Selection and indexing

`--scan-limit` counts prepared image rows. Stable hash sampling uses each image
location and the requested seed. Each row already contains all eligible source
associations, including objects and documents. The app applies its supported
image format and size limits, then copies the prepared titles, associations and
filter rows. It does not infer fields, parse dates or re-evaluate source rights.

The index generation records dataset repository, revision and contract version.
A permanent collection refuses selections from another release. Plain indexing
resumes saved rows without downloading or selecting the dataset again. Existing
indexes retain their own cached files and can continue to be served.

## Metadata refresh

`cronjob/refresh_image_metadata.py --generation RUN_ID --scan-limit N` loads
prepared metadata from the configured dataset. Every selected image must occur
within the scan limit with its original checksum. It copies prepared associations
and filter metadata, records the new release identity and reconciles existing
Qdrant payloads. Images are not re-embedded. Retrying reconciles an interrupted
payload update. Use a new collection for releases that change image identities
or membership.

## Special date rule: circa year

The published search contract includes the existing `c.YEAR` convention: when
structured bounds are absent, `c.1993` filters as 1993–1993 and the source display
text remains approximate. Creation dates take precedence over root dates; valid
ISO-date bounds and plain years are also supported by the producer. Unsupported
text and invalid/partial bounds produce no interval. This repository consumes
those intervals and retains only query validation and filter evaluation.
