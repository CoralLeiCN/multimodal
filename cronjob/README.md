# Data preparation scripts

## Sample and index images

Install the Python workspace and start Qdrant from the repository root:

```sh
uv sync --locked --all-packages
docker compose up -d qdrant
```

Inspect a reproducible sample, then ingest it:

```sh
uv run --package multimodal-backend cronjob/index_images.py --limit 50 --scan-limit 1000 --dry-run
uv run --package multimodal-backend cronjob/index_images.py --limit 50 --scan-limit 1000
```

The defaults select up to 50 distinct images from the first 1,000 object records,
using a stable hash sample with seed 42. Only selected image files are decoded.
Original metadata is streamed and the full image archive is not loaded. The
selection skips missing, ambiguous, corrupt, and unresolved-rights images. A
shorter sample is reported if fewer eligible files are available. Paths are
matched in full against the image root and its immediate extraction folders.

`--metadata PATH` can be repeated for specific bronze exports. `--seed` changes
the sample, `--scan-limit` accepts up to 10,000 records, and `--limit` accepts up
to 1,000 images. `--prepare-only` saves the selection snapshot and SQLite tracking
rows without contacting Gemini or Qdrant.

Each run prints its ID. Resume an interrupted or prepared run with:

```sh
uv run --package multimodal-backend cronjob/index_images.py --resume RUN_ID
```

The ignored root `.env` supplies `GEMINI_API_KEY`. Indexing uploads the selected
thumbnails to Google and stores each successful embedding in a SQLite cache before
upserting it to Qdrant. Stable point IDs make retries safe. An image becomes
`indexed` only after its Qdrant point is confirmed. A complete generation becomes
active in one SQLite transaction; failed runs retain the previous active index when using separate collections.
Permanent collection updates remain unavailable until successfully resumed.

Outputs live under `data/search/`: `catalog.sqlite3`, `selections/*.jsonl`,
`runs/*.json`, and persistent Qdrant storage. Each JSONL selection row includes
the local image path, checksum, source associations, licence, copyright, credit,
and `filter_metadata`. Associations include original `date` text, `places`,
`categories`, and derived `date_ranges`. Qdrant receives normalized filter rows
and four payload indexes for place/category keywords and start/end years.
Exit `0` indicates success and `2` indicates an invalid or failed run.

See [the backend guide](../backend/README.md) for API and configuration details.

## Permanent collection

Set `QDRANT_COLLECTION_NAME=smg_images` in `.env` to keep one collection across
indexing runs. The example configuration enables this setting. If omitted, each
run creates a separate collection using `QDRANT_COLLECTION_PREFIX`.

To reuse an existing collection, set `QDRANT_COLLECTION_NAME` to its exact name
and keep its original `SQLITE_PATH`. New runs reuse that collection's generation
and add or update selected images while retaining earlier images. Stable image
IDs prevent duplicates and cached embeddings avoid repeated embedding requests.
The selection limit applies to each sample; the accumulated collection may be larger.
Changing the seed changes the sample but does not guarantee new images.

```sh
make preview-index LIMIT=500 SCAN_LIMIT=10000
make index LIMIT=500 SCAN_LIMIT=10000
```

Updates happen in place. Stop the API before ingestion or preparation and restart
it after completion. The API checks the catalogue status on requests and blocks
access while the active collection is building or failed; stopping it also avoids
requests already in flight during an update. Resume failures with the printed
run ID. `--prepare-only` also marks an existing permanent collection as building.
There is no rollback to a previous version of that same collection.

The permanent collection requires the original embedding model and dimensions.
A populated collection from another SQLite catalogue is rejected. Keep SQLite
and Qdrant together when backing up or moving the index. Existing collections
are not renamed, merged, or deleted automatically. Delete an obsolete collection
only after the retained collection is complete and includes the images you need.
Selection snapshots and reports use the retained generation ID and are replaced
on subsequent runs; snapshots contain the accumulated catalogue.

## Refresh search filter metadata

Backfill date, place, and category metadata for an existing prepared or indexed
sample without reading image files or making embedding requests:

```sh
uv run --package multimodal-backend cronjob/refresh_image_metadata.py --generation RUN_ID
```

The command defaults to object exports under `data/bronze/` and scans at most
1,000 records across those files. Repeat `--metadata PATH` to supply the original
exports, or set `--scan-limit` from 1 to 10,000. It uses the saved source filename
and record ID to find every selected association. If any are missing from the
bounded scan, it exits before changing sample metadata. The SQLite migration
may already have added empty columns.

Successful refreshes update SQLite fields and `selections/RUN_ID.jsonl`, ensure
the four Qdrant payload indexes exist, and replace metadata on existing points.
Embedding vectors, cache entries, point IDs, and ingestion statuses are retained.
A prepared sample receives an empty Qdrant collection with payload indexes and
remains pending until embedding ingestion completes.

Stop the API before refreshing a published sample, then restart it after success.
The command shares the ingestion writer lock. An interrupted Qdrant payload
update can be repaired by rerunning the same command; SQLite retains the desired
metadata. The report contains `index_version`, `scanned`, `images`,
`payloads_updated`, and `payload_indexes`. Exit codes are `0` for success and `2`
for invalid inputs or failure. See [the source field mapping](../data_spec.md#search-filter-metadata)
and [filter semantics](../docs/multimodal_search_spec.md#metadata-filters).

## Convert silver CSV to gold Parquet

Install the locked environment and run:

```sh
uv sync --locked
uv run cronjob/silver_to_gold.py
```

Default input:
`data/silver/smg_object_records_with_CC_images_09_04_2025.csv`

Default output:
`data/gold/object_records.parquet`

Default image directory: `data/images/`, searched recursively.

The input is the official processed CSV downloaded from SMG. Its columns are
`uid`, `identifier`, `title`, `description`, `category`, `material`, `object_name`,
`date`, `place`, `maker`, and `image`.

The converter reads the CSV into a pandas DataFrame and writes it with
`DataFrame.to_parquet(engine="pyarrow", compression="zstd", index=False)`.
CSV parsing uses pandas' Python engine and Arrow-backed string columns.
Only rows whose `image` reference matches a local image file are included in
gold. Matching checks the full relative path and supports extracted archive
folders. A row is retained if at least one matching file exists. Blank, invalid,
or missing image references are removed. A missing image directory is an error.
The converter reports source, retained, and removed row counts.

Retained rows keep their source order, including duplicate rows and empty columns.
All columns use the Parquet string type. Empty CSV cells stay
empty strings; values such as `NA`, leading zeros, and date ranges keep their
original text. Quoted commas and line breaks in descriptions are supported.

The DataFrame's `attrs` records the silver source path, its SHA-256 checksum,
the image directory, and source and removed row counts;
pandas saves these attributes in the Parquet metadata and restores them on read.
The converter reads back the Parquet with pandas and checks every retained value,
column, data type, and attribute. It confirms the input checksum is unchanged before
replacing the destination file. Conversion and verification hold the data in
memory. A failed conversion
leaves any existing gold file available. Exit code `0` indicates success; `2`
indicates an input or conversion error.

To use different paths:

```sh
uv run cronjob/silver_to_gold.py \
  --input data/silver/smg_object_records_with_CC_images_09_04_2025.csv \
  --output data/gold/object_records.parquet \
  --images-dir data/images
```

For full measurements, additional descriptions, or per-image licence details,
look up the `uid` in the bronze JSON exports or use the generated image manifest.
See pandas' [CSV reader](https://pandas.pydata.org/docs/reference/api/pandas.read_csv.html)
and [Parquet writer](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_parquet.html)
documentation for the file operations used by this converter.

## Optional image metadata audit

This utility creates the `data/processed/` directory and its outputs on demand.
Generated manifests and reports can be removed after review and rebuilt from
the bronze JSON and local images.

Run from the repository root with Python 3.12 or newer (no packages to install):

```sh
python3 cronjob/match_images.py
```

The script streams `data/bronze/smg_*_records_with_CC_images_*.json`, indexes images recursively under
`data/images/`, and matches each `multimedia` entry using its
`@processed.medium_thumbnail.location`. It checks the full relative path, so
extracted wrapper folders (including `smg_all_medium_thumnail_images_09_04_2025`)
work without renaming. Filenames alone are not used to establish a match.

Outputs:

- `data/processed/image_manifest.csv`: one row per record–image association,
  including record/image IDs, title, primary description (or first available),
  absolute local path, matching status, image licence/attribution fields, dates,
  makers, catalogue identifiers, measurements, classification, archival level,
  and thumbnail dimensions.
- `data/processed/image_match_summary.json`: per-dataset and overall counts,
  including distinct image paths and records with at least one matched image.

Both datasets always use the same CSV columns. Keep catalogue identifiers, dates,
makers, measurements, classification, archival level, and description even when
their values are empty, including when an entire dataset has no values for a
column. Missing values are blank cells. An image association is retained even
when any of these metadata fields is empty.

The additional metadata columns are:

| Column | Contents |
| --- | --- |
| `date` | Distinct supplied date values from `creation.date` followed by top-level `date`, retaining their source formatting. These can include different date roles. |
| `maker` | Display names of all `creation.maker` entries, using `summary.title` or the primary/first `name`. |
| `catalogue_identifiers` | All distinct `identifier.value` values, including accession and inventory numbers. |
| `measurements` | Supplied display text and dimension values, with labels and units, including weight where provided. |
| `classification` | Category names, falling back to category values. |
| `archival_level` | The archival `level.value`, such as `fonds`. |
| `thumbnail_width`, `thumbnail_height` | Pixel dimensions of this row's medium thumbnail from the JSON, including for missing image files. |
| `metadata_details_json` | Original structures for the selected record fields, encoded as compact JSON in a CSV cell. |

Readable columns join distinct values with `; ` in source order; blank means the
value was not supplied. For exact list boundaries, date ranges and roles, maker
references, identifier types, measurement details, or source notes, parse
`metadata_details_json` with `json.loads()`. It retains top-level `date`,
`identifier`, `measurements`, `category`, and `level`, plus `creation.date` and
`creation.maker`, wherever present. It is a subset of the source record, not the
entire original JSON. Thumbnail dimensions belong to each image individually;
they are not copied from the first image of the record or measured from the JPEG.

Rows with `status=matched` have a local image available for image/text indexing;
also check their individual licence before reuse, as explained in the
[data and licensing notes](../data_spec.md). Other statuses are
`missing` (referenced file absent), `ambiguous` (multiple local copies match),
`no_thumbnail` (no medium thumbnail location), and `invalid_location`.
Ambiguous candidates are listed in `candidate_paths`; no copy is picked automatically.
A shared image can appear in several rows because it belongs to several records.
Distinct-image counts use thumbnail paths, not row counts.

All media entries are reported, including entries without a thumbnail path.
Records with no media entries appear only in the summary record counts.
Matching checks file presence and paths; it does not decode images or validate
their contents. Licence fields are preserved for review, not filtered or approved.
Original JSON exports remain the source for other metadata fields.

The script makes no downloads. Outputs are rebuilt on each run and replaced only
after all input JSON files parse successfully. Avoid overlapping scheduled runs.
Exit codes: `0` for a completed audit (missing images are expected in this download),
`1` with `--fail-on-missing` if any media entry cannot be matched, and `2` for an error.

To process only objects, or use other directories:

```sh
python3 cronjob/match_images.py \
  --metadata data/bronze/smg_object_records_with_CC_images_09_04_2025.json \
  --images-dir data/images \
  --output-dir data/processed
```

The defaults are relative to the script's repository, so it also works when called
from another working directory. If scheduling with cron, use absolute paths to
your Python executable and this script. Creating this script does not install a cron job.

Run the focused checks with:

```sh
uv run --with pytest pytest cronjob/
uv run --with ruff ruff check cronjob/
```

Tests use pytest fixtures for temporary metadata and image files. Ruff checks
the standalone script against Python 3.12 compatibility.
