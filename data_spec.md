# Science Museum Group data and licensing

## Downloaded data

This project uses the `with_CC_images` exports from the
[Science Museum Group datasets page](https://coimages.sciencemuseumgroup.org.uk/datasets/index.html):

- `data/bronze/smg_object_records_with_CC_images_09_04_2025.json`
- `data/bronze/smg_document_records_with_CC_images_09_04_2025.json`
- Official processed CSV:
  `data/silver/smg_object_records_with_CC_images_09_04_2025.csv`.
- Images from `smg_all_medium_thumbnail_images_09_04_2025.zip`, extracted under
  `data/images/smg_all_medium_thumnail_images_09_04_2025/`.

The spelling `thumnail` is the extracted directory's actual name.

The bronze layer preserves the original JSON exports for reference. The silver
layer contains the official processed CSV. `cronjob/silver_to_gold.py` uses pandas
to convert that CSV into `data/gold/object_records.parquet`, with PyArrow as the
Parquet engine.
Gold includes silver rows whose `image` references match local files under
`data/images/`. It retains all columns and text values for those rows, including
empty strings. The converter reports how many rows were removed.
The optional image matching script generates a richer manifest and coverage
report in `data/processed/` on demand. These outputs can be removed after review
and rebuilt from bronze. Their fields are documented in [the pipeline guide](cronjob/README.md).

SMG provides these exports for academic or personal research. It directs users
to check each image's licence: a `with_CC_images` record can contain additional
images with different or unspecified rights. The export name is a selection of
records, not a blanket licence for every associated file.
[Source: SMG usage guidance](https://coimages.sciencemuseumgroup.org.uk/datasets/index.html).

## What CC means

**CC means Creative Commons**, a family of standard copyright licences. The
letters specify the conditions attached to a work:

| Term | Meaning |
| --- | --- |
| BY — Attribution | Credit the supplied creator/attribution parties, retain applicable notices, link to the source and licence, and indicate changes. |
| NC — NonCommercial | Use under this licence must not be primarily intended for commercial advantage or monetary compensation. |
| SA — ShareAlike | When sharing adaptations, use the same or a compatible licence. |
| ND — NoDerivatives | Sharing adapted material is not permitted under this licence; sharing the original is allowed subject to the other conditions. |

For **[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)**,
attribution, noncommercial use, and ShareAlike apply. For
**[CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/)**,
attribution and noncommercial use apply, and adaptations must not be distributed.
Both licences prohibit additional restrictions on the licensed freedoms and do
not permit implying the rights holder endorses this project.

SMG also lists **[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)**
for some images. OGL is a separate licence, not a CC licence. It permits reuse
and adaptation, including commercial reuse, subject to its terms, including
source acknowledgement and linking to the licence where possible.

## Metadata and text

SMG assigns **[CC0](https://creativecommons.org/publicdomain/zero/1.0/)** to the
title, made, maker, and details fields. CC0 dedicates applicable copyright and
related rights to the public domain to the extent permitted by law.
Descriptions and other text use **[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**,
which requires attribution, a licence link, and disclosure of modifications.
These metadata terms are separate from each image's licence.
[Source: SMG metadata terms](https://coimages.sciencemuseumgroup.org.uk/datasets/index.html).

## Handling followed in this project

We obtained the supplied bulk exports and thumbnail archive. The local matching
workflow follows SMG's published method: it associates each image with its JSON
record using `multimedia[*].@processed.medium_thumbnail.location`.
It operates entirely offline and leaves the source JSON and image files unchanged.
SMG prohibits harvesting IIIF/Zoom endpoints and discourages unnecessary bulk
downloads of larger images; this script accesses neither.
[Source: SMG image guidance](https://coimages.sciencemuseumgroup.org.uk/datasets/index.html).

The image manifest generated from bronze preserves the supplied image `licence`, `copyright`, and `credit`
values, plus the source JSON and record/image identifiers. The original JSON
retains the complete rights structures and other source metadata.

The generated image manifest also includes dates, makers, catalogue identifiers, measurements,
classification, archival level, and each thumbnail's pixel dimensions.
These columns and `description` remain in the CSV for both objects and documents,
including when values are absent. Missing values are represented by blank cells;
the columns and corresponding image associations are retained.
`metadata_details_json` retains the original structures for those selected
record fields, including date ranges, roles, units, and notes. See the
[column definitions](cronjob/README.md) for exact source mappings.

The official silver CSV and its gold Parquet contain the eleven columns supplied
by SMG. Per-image licence, copyright, and credit information is available in
bronze and the image manifest; follow the source record's `uid` when reviewing
rights for a gold row.

Project attribution:

> © The Board of Trustees of the Science Museum.
> Source: [Science Museum Group Collection](https://collection.sciencemuseumgroup.org.uk/).

When presenting or redistributing an image or description, carry its applicable
credit and notices into that output, link to the source record and the correct
licence above, and identify modifications. Apply NC, SA, or ND according to that
individual image's licence. The repository attribution accompanies this project;
the same information must also accompany relevant downstream uses.

## Verified coverage and remaining licence checks

Local audit on 18 September 2026:

| Measure | Count |
| --- | ---: |
| Local image files matched to metadata | 149,232 |
| Distinct thumbnail paths referenced in both JSON files | 240,615 |
| Referenced thumbnail paths missing locally | 91,383 |
| Record–image associations with a local match | 152,214 |

The matched associations contain the following **supplied licence values**:

| Licence value in the JSON | Matched associations |
| --- | ---: |
| `CC BY-NC-SA 4.0` | 151,679 |
| `CC-BY-NC-SA 4.0` (source spelling) | 8 |
| `CC BY-NC-ND 4.0` | 73 |
| `Open Government Licence v3.0` | 6 |
| No licence value supplied | 448 |

These are association counts, not distinct-image counts: the same image can
appear in several records. The 448 associations without licence values need
their rights established before reuse; the filename or export name supplies no
substitute. Keep these entries out of reuse selections until that check is done.

`status=matched` confirms a local path, not licence clearance. The script reports
all associations and preserves rights fields; it does not filter them by licence.
The verified steps above document sourcing, matching, and retention of attribution.
Full compliance for a particular use also depends on that use meeting the
individual licence conditions.

Run `python3 cronjob/match_images.py` to refresh the manifest and coverage counts.
See [the script documentation](cronjob/README.md) for the output format.

## Search filter metadata

The search ingestion service reads filter metadata from the bronze JSON for its
bounded image selection. It preserves identifiers, original date display text,
and image rights in SQLite and the JSONL selection snapshot.

| Search field | Source and interpretation |
| --- | --- |
| `places` | `creation.place[].summary.title`, falling back to the first nonempty `name[].value`. This describes creation places. Preserve full labels and do not infer geographic parents. |
| `categories` | `category[].name`, falling back to `category[].value`. |
| `date_ranges` | `creation.date[]`, falling back to top-level `date[]` only when no creation dates exist. Use valid `from` and `to` years; ISO date bounds are reduced to years. If both bounds are absent, an exact year or ISO date in `value` is accepted. |

Duplicate labels compare by Unicode NFKC normalization, collapsed whitespace,
and case folding. Original display labels remain in SQLite. Dates must have
nonzero integer years between -9999 and 9999 and a start no later than the end.
Partial, reversed, unknown, or unparseable dates contribute no interval. Free
text such as “circa 1850” is not interpreted without structured source bounds.
Keep disjoint ranges and separate record associations distinct. Raw date text
still includes all supplied creation and top-level date values for display.

Qdrant stores normalized place/category keywords and numeric start/end years in
nested metadata objects, with one object per association and date interval.
Date filters use inclusive overlap. Missing metadata remains searchable without
that field's filter. See [the filter contract](docs/multimodal_search_spec.md#metadata-filters)
and [metadata refresh command](cronjob/README.md#refresh-search-filter-metadata).

With `QDRANT_COLLECTION_NAME` set, search ingestion keeps an accumulated catalogue
in the same generation and Qdrant collection. Each sample adds or updates its
selected image rows, source associations, and filter metadata; images outside
that sample remain present. Selection snapshots contain the accumulated catalogue.
See [permanent collection setup](cronjob/README.md#permanent-collection).

The shared `catalogue/catalog.sqlite3.gz` snapshot is tracked in Git LFS. It
preserves the complete catalogue, including metadata and image attribution.
Local ingestion stores cached vectors in the separate
`data/search/embedding_cache.sqlite3` database. Migration `0003` transfers legacy
cache entries and removes the old cache table and its unused pages from the
catalogue. Subsequent exports copy the catalogue without filtering its rows.
See the
[export and restore guide](catalogue/README.md); the corresponding Qdrant
collection and local thumbnails are required to use a restored index.
