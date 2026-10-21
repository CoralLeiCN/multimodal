# Collection dataset

The application consumes the Science Museum Group release prepared by the
`smg-datasets` repository. Original exports, curation decisions, schema research,
metadata normalization, image matching, release validation and publication are
maintained there. App indexing, embeddings, PostgreSQL and Qdrant remain here.

The eventual Hugging Face ID is `YOUR_ACCOUNT/YOUR_DATASET`; this is a placeholder,
not a published repository. Configure the actual ID and immutable commit revision
when supplied. See [the consumption contract](data_processing_spec.md) and
[indexing commands](cronjob/README.md).

Package schema 3.0.0 provides `objects`, `documents`, `images` and `catalogue`.
The app uses `catalogue` version 1.0.0: one image location with original bytes,
prepared filter metadata and all eligible object/document associations. The
producer preserves source identities, licences, credits and checksums. Records
without eligible images remain in the object/document configurations.

Downloaded Parquet uses the standard Hugging Face cache. Selected original bytes
are cached under `IMAGE_ROOT` (default `data/hf-images`), while indexing reports,
selection snapshots and the embedding cache remain under `data/search`. Generated
assets remain ignored by Git. Existing raw downloads can be retained locally for
reference; application commands no longer read them.

## Source and attribution

Original sources: [SMG bulk datasets](https://coimages.sciencemuseumgroup.org.uk/datasets/index.html)
and [SMG Collection](https://collection.sciencemuseumgroup.org.uk/).
Museum content retains its source terms. The published dataset card and each
association retain the applicable image licence, credit and copyright. Code
licensing does not relicense collection metadata or images. Image eligibility is
determined by the producer's recorded policy; the app does not infer permission
from file availability or the source export's name.

Attribution: © The Board of Trustees of the Science Museum.
Source: [Science Museum Group Collection](https://collection.sciencemuseumgroup.org.uk/).

## Planned Hugging Face dataset publication

Publication tooling is maintained in `smg-datasets` as
`python -m smg_datasets.publish_huggingface`. The default command validates and
prints a plan; `--publish` performs the upload. The returned commit SHA pins the
application release. Publication has not been performed as part of this migration.
