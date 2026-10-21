# Bronze tabular schema proposal

Status: **design only**, version `smg_bronze_tabular/0.1.0-proposed`,
20 October 2026. The proposed input is the original SMG object and document
JSON. The official CSV does not define this schema.

Use linked Parquet tables to expose collection facts while retaining each
complete source record and the original export bytes. Bronze preserves source
assertions, their ownership and their order. Later processing can resolve
entities, interpret dates, convert units and select images for search.

This proposal does not change the existing converter, catalogue or indexing
service. The current processing rules remain documented in
[data_spec.md](../../data_spec.md) and
[data_processing_spec.md](../../data_processing_spec.md).

The design has three parts:

- This document explains scope, row identity, ownership, preservation decisions,
  source evidence and checks required before implementing an extractor.
- [field_catalog.json](field_catalog.json) defines 15 tables, including one
  optional annotation table, their column types, meanings, source mappings,
  units, nullability and normalization policy.
- [source_decisions.json](source_decisions.json) records a treatment and rationale
  for every observed top-level key, with per-export presence counts and specific
  nested exceptions.

## Evidence and coverage

The research read the original exports under
`/Users/coral/repos/multimodal/data/bronze/`. Files were streamed in full for a
structural inventory; the source bytes were not changed. Objects provided the
first field families. The archive export was checked in the same study because
its hierarchy, rights prose, arrangement and measurements differ substantially.

| Input | Records | Bytes | Top-level keys | Distinct structural paths |
| --- | ---: | ---: | ---: | ---: |
| `smg_object_records_with_CC_images_09_04_2025.json` | 150,337 | 2,149,281,699 | 49 | 1,041 |
| `smg_document_records_with_CC_images_09_04_2025.json` | 7,017 | 159,164,145 | 26 | 319 |
| Combined | 157,354 | 2,308,445,844 | 57 | 1,131 |

Structural paths replace array positions with `*`; the counts include container
paths and the record root. Key presence does not imply a nonempty or useful
value. All top-level records have a UID, unique within each inspected export.
A separate full parse found no duplicate JSON object keys. The inventory found
no mixed primitive/container types at the same wildcard path within either
export; corresponding paths across the two exports can still differ.

Source byte SHA-256 values:

```text
objects   ee00cdb4f9f5833429eab1560cf91a5d0f80311c1277686b90c92d03e62a18d5
documents e11f026dca506c74728eddc22ce4c15c4e481d79983641992940bab19337716c
```

Semantic investigation used 40 exact excerpts from 28 records, selected for
different source shapes, rare fields, conflicting dates, missing values and
unclear meanings. This is sampled semantic review, not individual review of all
157,354 records. The evidence index below identifies the source record and exact
JSON pointer for every excerpt. Full inventory reports, selected fragments and
research scripts are local, ignored artifacts under
`data/processed/bronze_schema_research/`.

Some examples of useful coverage, counted as records with the key present:

| Source field | Objects, out of 150,337 | Documents, out of 7,017 | Why it matters |
| --- | ---: | ---: | --- |
| `description` | 150,337 | 4,285 | Retain all types and occurrences; missing archive descriptions are expected. |
| `inscription` | 37,137 | 0 | Transcriptions, labels, translations and placement can support text retrieval. |
| `component` | 7,324 | 217 | Measurements can describe individual components. |
| `creation` | 126,315 | 7,008 | Dates, makers and places retain their lifecycle context. |
| `cumulation` | 75,176 | 0 | Collector and collection context is distinct from manufacture. |
| `parent` | 20,296 | 6,503 | Relationships often point outside the exported subset. |
| `legal` | 150,337 | 4,748 | Record-level rights and credit are distinct from media rights. |
| `arrangement` | 4 | 155 | Object records use scalar text; archive records use lists. |
| `accrual` | 0 | 49 | Archive narratives should not disappear because they are uncommon. |
| `web` | 0 | 88 | References can contain network paths, not public URLs. |

There are 222,493 top-level media occurrences in object records and 24,077 in
document records. These are association counts, not distinct files. Additional
media inside embedded child references belong to those child occurrences. One
document record contains a PDF original (E26); a schema restricted to thumbnails
would discard it.

## Storage and row identity

Proposed output location:

```text
data/bronze/tabular/<build_id>/
  manifest.json
  source_files.parquet
  records.parquet
  texts.parquet
  identifiers.parquet
  attributes.parquet
  events.parquet
  entity_references.parquet
  dates.parquet
  components.parquet
  measurement_sets.parquet
  inscriptions.parquet
  media_occurrences.parquet
  media_representations.parquet
  rights_statements.parquet
  annotations.parquet           # optional; empty or absent until reviewed
```

Original JSON remains under `data/bronze/`. A portable release includes those
artifacts at the relative locations recorded in its manifest. Each build is
immutable and records the schema version and catalog hash, extractor version,
source hashes, output hashes, row counts, build time and structural warnings.
Repeated captures can reference the same source content hash while retaining
their separate acquisition observations in build manifests. Unknown acquisition
times remain null; the filename date and filesystem modification time are not
substitutes.

Keys have different purposes:

| Key | Meaning |
| --- | --- |
| `source_file_id` | SHA-256 of the exact original file bytes. |
| `snapshot_id` | Hash of `["smg-bronze-record-v1", source_file_id, record_ordinal]`. One source occurrence, including repeated UIDs if a future export contains them. |
| `row_id` | Hash of `["smg-bronze-row-v1", snapshot_id, table_name, source_pointer]`. One occurrence in a particular projection table. |
| `record_uid` | Original SMG identifier. Use namespace plus UID for possible cross-release linking; it does not replace snapshot identity. |
| `source_media_uid` | Original media identifier. It is distinct from the search catalogue's generated image UUID. |

Hash inputs use UTF-8 JSON arrays with no optional whitespace and no ASCII
escaping, preserving element order. Hashes are lowercase SHA-256 hex strings.
Record ordinals and array indices are zero-based. Child-table pointers are
relative to the source record; prefix them with `/<record_ordinal>` to locate
the value in the original export. Pointer tokens escape `~` as `~0` and `/` as
`~1`.

Generated IDs remain stable for the same input bytes, source pointers and schema
mapping. They are deliberately snapshot-specific. A changed export can preserve
an SMG UID while producing a new snapshot ID.

## Proposed tables

The JSON catalog is the detailed field definition. Source strings and codes have
open vocabularies; adding a previously unseen value must not discard a row.
All fields have null unit unless the catalog explicitly supplies a unit.
Measurements carry their supplied units alongside their values.

| Table | One row represents | Principal columns beyond provenance |
| --- | --- | --- |
| `source_files` | An immutable source file | Filename, namespace, family, byte size, hash, known origin/capture information, record count. |
| `records` | A top-level object or archive record occurrence | Source UID/ID/UUID/system, datatype and source flags, summary title, archival level, source descendant counts, complete `record_json`. |
| `texts` | An individual text statement | Text kind, exact value, type, supplied primary flag, language and attribution. Includes all descriptions, captions, archive notes, arrangement and reference text. |
| `identifiers` | An identifier assertion | Exact value, identifier kind/type, primary flag and issuing source. Includes barcodes. |
| `attributes` | A classification, material, physical or source-code assertion | Family, original value, name/term, type, component label, museum code and notes. Keeps undecoded fields under explicit source names. |
| `events` | A lifecycle or location container | Source family, source label, narrative, type and location string. Owned dates and participant references live in their respective tables. |
| `entity_references` | An embedded reference or literal occurrence | Relationship kind, source target IDs, label/names, complete link qualifiers and hierarchy positions. |
| `dates` | A date assertion | Raw value/from/to, context, roles, notes, source, primary flag, method and explanation. |
| `components` | An explicitly named component | Component name and original name structure. |
| `measurement_sets` | A measurement group | Display/count text, ordered dimension structs, filesize structure and notes. |
| `inscriptions` | An inscription or label | Type, location on object, method, source inscriber text, ordered detail structs, languages and notes. |
| `media_occurrences` | A media association with its record or embedded target | Source media IDs/type, source position and upload-sort string. |
| `media_representations` | One supplied rendition | Media occurrence ID, rendition name, unchanged location, type/format, resizable flag and source modified value. |
| `rights_statements` | An individually scoped right, credit or access statement | Statement kind, original licence/copyright/holder/type, statement/details and credit/access value. |
| `annotations` | An optional review finding for a row or field | Evidence state, issue code, exact evidence pointers, rationale and method version. |

Dimensions are `LIST<STRUCT>` values with source pointer, exact JSON value,
dimension label, unit, source type, primary flag and original fragment. This
keeps `2 5/16 in`, `58 mm`, unlabeled values `61` and `78`, and `1 item` distinct
(E08, E23, E02). Inscription details are ordered structs containing transcription,
transliteration, all translation entries and their original fragment (E09, E25).
These nested columns are part of the Parquet table; later SQL views can unnest
them without changing bronze.

Every occurrence table carries its snapshot ID, source pointer, owner table/ID,
owner pointer, array position, complete original fragment and structural field
states. Whole-record JSON provides coverage for every source path, including
fields without dedicated columns. Original export bytes remain the authority
for exact serialization; JSON payload columns preserve values, primitive types,
strings and array order rather than claiming byte-identical whitespace.

## Ownership and multiplicity rules

1. **Retain each occurrence.** Equal strings, repeated IDs and duplicate-looking
   descriptions remain separate when their source pointers differ. E05 contains
   `particle accelerator` as both `catalogue name` and `object name`. Neither
   occurrence is removed. A later display view may deduplicate its labels.
2. **Use the nearest semantic owner.** Top-level descriptions belong to the
   record. Media captions belong to a media occurrence. Component measurements
   belong to the component. `@link` qualifiers belong to a reference occurrence.
   Every non-record owner must exist in its declared table and share the snapshot.
3. **Retain referenced-target scope.** An embedded child is a reference occurrence,
   not a full record snapshot. Its identifiers, nested children and singleton
   media object remain owned by that reference (E13). A missing target record
   does not remove the reference or establish that the target does not exist.
4. **Preserve association context.** `creation.maker`, generic `agent`, `subject`,
   `institution`, collector and owner references are separate relationship kinds.
   A shared name does not establish shared identity. `@entity=literal` does not
   receive a fabricated authority UID. Placeholder IDs such as `cd` stay visible
   as source claims and require later resolution (E06, E11, E33).
5. **Keep dates attached to their statement.** `creation.date`, top-level `date`,
   `@link.date`, `@link.attribution.date`, identifier dates and rights dates all
   survive. An attribution date of 2015 is not an object's manufacture date
   (E20). Both 1949 and 1950 survive for E21/E21b, under their original contexts.
6. **Preserve grouping without inventing pairings.** The three dates, three maker
   occurrences and two place occurrences in E01 remain independently ordered
   children of the creation container. Do not create 18 combined event rows or
   assert a one-to-one correspondence that the source does not provide.
7. **Retain hierarchy topology.** Top-level `@hierarchy` is an array of paths;
   facility hierarchy is a flat list. Preserve path boundaries, order and self
   nodes. `@hierarch`, `parent`, `fonds`, `grouping` and `child` remain distinct
   source statements. Do not derive authoritative parent edges merely from a
   traversal order (E10, E12, E34).
8. **Scope media and rights to occurrences.** Keep repeated media IDs in separate
   associations; their captions, credit and rights can differ. Record rights do
   not automatically apply to media, and a shared image ID does not authorize
   overwriting one association's rights with another's (E14, E24, E27).

Singleton objects and array items can both produce occurrence rows. The original
pointer, `source_ordinal=null` for singletons and complete fragment distinguish
the two forms. Never insert synthetic array index `0` into a source pointer.

Some convenience columns repeat content also exposed as owned rows: for example,
`entity_references.names_json` and corresponding name texts. Such columns are alternate
projections of the same source pointers, not additional assertions. Count
assertions within a selected table and family; do not sum nested copies and
their projected rows.

## Preservation and omission decisions

No source metadata is proposed for irreversible deletion. The decisions below
control which fields receive typed columns and what interpretation is deferred.
The machine-readable decision register covers all 57 observed top-level keys.

| Decision | Treatment | Reason and evidence |
| --- | --- | --- |
| D01: Keep all records and media | Retain regardless of local file presence, missing title, missing rights or supported search format. | Bronze describes the supplied collection, not the current search sample. E26 is a PDF; E38 has no title key. |
| D02: Retain original payloads | Preserve complete record JSON plus immutable original exports. | Unknown fields and future mappings remain recoverable. Typed columns alone cannot claim complete semantic interpretation. |
| D03: Preserve every text variant | Keep type, primary flag, language, ordering, markup and spelling. | Catalogue/web descriptions and inscriptions have different meanings (E03, E09). No summary or primary-only reduction. |
| D04: Keep identifiers and qualifiers | Preserve names, IDs, role, source, component and attribution independently. | Combining values with punctuation loses boundaries and provenance (E04, E06, E20). |
| D05: Defer date interpretation | Store exact bounds and text; add interpretations in a later layer. | E22 has `value=1837-01` but bounds `1837` to `1801`; E37 has BCE/CE text. Bronze must not repair or reject them. |
| D06: Defer numerical measurement parsing | Keep fractions, display strings, dimensions, units and scope. | E23 supplies `61` and `78` without labels or units. Guessing width/height or millimetres would invent facts. |
| D07: Defer entity resolution | Preserve reference and literal occurrences and their source labels. | E06 has one combined literal maker name plus two named references. Splitting on semicolons or merging by label is not justified. |
| D08: Preserve unclear source fields | Expose `source_sex`, `source_age`, culture codes and options as source assertions, with unresolved semantics. | `sex.value` contains `Ferranti; John Belshaw`; age can contain `multiple not home`. Culture has both opaque codes and names (E16, E17, E31). |
| D09: Omit analytics from typed facts | Keep `enhancement` and `@admin.enhancement` in record JSON only. | Web traffic and analytics processing are outside the initial collection-content schema. The retained payload permits future analytics research. |
| D10: Omit source workflow markers from typed facts | Keep `@admin.sequence` and `conformance` in record JSON only. | Sequence is not a stable cross-export ID; conformance is not this project's verified quality score. |
| D11: Separate access meanings | Project access notes, copies, originals and conditions; preserve `access.rights` permission objects only in raw payload. | Source `APIPool`/`Read` entries should not become a public reuse-licence field. Physical access notes remain useful collection context. |
| D12: Preserve record and media rights independently | Keep every original right, credit, holder, statement, qualifier and date. | Different scopes and uncertain rights prose occur in E14, E24 and E27. No clearance flag is inferred. |
| D13: Keep historical location context | Preserve facilities, display locations, shelf strings and associated dates. | The export is a snapshot; a gallery label is not a verified current location or creation place (E12, E18). |
| D14: Keep all renditions as metadata | Preserve every `@processed` member, including `original` and `zoom`. | A rendition is distinct from a local file. Retaining its metadata does not require fetching its bytes (E24, E26). |
| D15: Keep raw reference text | Preserve `web` strings without inventing public URLs. | E15 is a network file path. Existence of a string is not evidence that it is a reachable link. |
| D16: Retain empty structures and source counts | Distinguish absent keys, nulls and empty containers; preserve supplied descendant counts. | E36/E39 contain explicit empty arrays. Exported children need not equal source descendant totals (E19). |
| D17: Report unmodeled paths | Keep them in record JSON and include their path, type and occurrence counts in the build report. | Future source changes must not silently disappear or be described as fully projected. |

There is no bronze date filter interval, geographic hierarchy inference,
licence normalization, entity deduplication or inferred measurement number in
this proposal. Those are candidate silver outputs, with their own rules and
versioned annotations. In particular, the existing `c.1993 → 1993–1993` search
convention does not replace the bronze source assertion.

## Missingness, uncertainty and errors

`field_states` records structural states for each projected source field:
`absent`, `null`, `empty_string`, `empty_array`, `empty_object`, `value`, or
`type_mismatch`. Empty strings stay empty strings; zero and false remain supplied
values. Empty child lists produce no invented child rows; the source record or
owner fragment retains their existence. Future type mismatches retain the
original fragment, emit a null typed projection and a diagnostic, rather than
coercing or dropping the source record.

Structural presence is different from interpretation. The optional annotations
table records `known`, `unknown`, `conflict` and `not_applicable` separately.
An absent annotation means `not_reviewed`. `known` means the statement has an
interpretable meaning under a named review method, not that its historical truth
has been independently established. `not_applicable` needs explicit evidence or
a documented applicability rule; absent evidence cannot establish it. A source
string such as `Unknown` remains verbatim even if a later annotation recognizes
its meaning.

Conflicting source assertions remain distinct rows. An annotation can point to
all conflicting pointers without selecting a winner. Source shape errors,
ambiguous semantics and contradictory values must have different issue codes.
Malformed JSON or duplicate object keys require an explicit ingest error and
preservation of the original artifact; a default JSON parser must not silently
collapse duplicate keys. An incomplete build cannot replace a complete release.

## Proposed semantic checks and implementation acceptance

The following checks have been exercised against the cited source fragments as
design examples. They are not claims that a new extractor has been implemented.

| Example | Required representation |
| --- | --- |
| E03/E05 | Keep both descriptions and both differently typed object-name entries. |
| E01/E06 | Retain all dates, makers and places with independent array order and creation ownership. |
| E07/E20 | Keep relationship dates and attribution dates outside object creation dates. |
| E08/E23 | Retain component ownership, fractions and missing dimension labels/units. |
| E10/E12/E34 | Keep archival paths, flat facility paths and fonds references without flattening their topology. |
| E13 | A child's singleton media reference belongs to the child occurrence. |
| E14/E24/E27 | Keep record rights, media rights and credit under their own owners. |
| E16/E17/E31 | Keep source codes and named culture statements without inventing meanings for ambiguous keys. |
| E21/E21b/E22/E30/E37 | Preserve conflicting, malformed, explained and BCE/CE date assertions. |
| E25 | Keep the transcription, translation array and transliteration separately. |
| E26 | Preserve the PDF original with no manufactured thumbnail or JPEG requirement. |
| E28/E35 | Preserve archive arrangement lists and object arrangement scalar text. |
| E36/E38/E39 | Preserve empty lists and records with absent titles or maker entries. |

Before publishing a tabular bronze build, the extractor must demonstrate:

1. Input byte hashes match the manifest; original files remain unchanged.
2. Both top-level record counts match exactly, including records without usable
   images, titles or rights. Repeated UIDs are reported and retained.
3. JSON payloads preserve every parsed property, primitive type and ordered
   array value. All source paths are accounted for by a projection, a recorded
   payload-only/deferred decision, or an explicit unmodeled-path diagnostic.
4. Every emitted pointer resolves to its preserved source fragment. Ordinal,
   owner, generated-key and foreign-key checks pass, including nested child
   references. Target UIDs outside the export are allowed.
5. Mapped occurrence counts reconcile by table, family and owner scope. Baselines
   distinguish top-level media from embedded-target media; they never compare
   association counts with distinct-image counts.
6. No sibling-array Cartesian products or implied date/place/person pairings
   appear. Nested list order and null/empty distinctions survive a Parquet read.
7. The semantic examples above pass, and all encountered shape mismatches,
   unresolved meanings and projection gaps appear in the report.
8. A failed write leaves the prior release intact. Publication occurs only after
   all tables, hashes and checks are complete.

The build manifest should report structural completeness separately from semantic
review coverage. Retaining a field in JSON satisfies preservation, but does not
claim that it has a useful typed projection or a settled meaning.

## Remaining design work

- Obtain source definitions for `sex`, `age`, `options`, culture codes and the
  attribution-like contents of `stratigraphic`. Until then, preserve their source
  names and mark interpretation deferred.
- Confirm the units/time basis of source `added`, `processed`, `modified` and
  `upload_sort` values before offering normalized timestamps.
- Define later entity resolution and date interpretation policies with evidence;
  these are not prerequisites for preserving bronze.
- Benchmark the full payload plus repeated fragment columns on the complete
  export. Zstandard compression is the initial storage choice, but compression
  does not share dictionaries across separate tables. If fragment copies prove
  excessive, a documented revision can resolve them through record JSON and
  pointers while retaining the same source evidence.
- Test a later export revision and, if available, actual agent/place/term
  authority exports. The current embedded references do not constitute complete
  authority records. Missing authority data does not block this initial schema.

Implementation should start with this catalog and the recorded source mappings,
then produce a small bronze build that passes the semantic cases before a full
conversion. Search should continue through the shared `SearchService`; adopting
this format later does not call for a separate collection or query-time indexing.

## Evidence index

`O` refers to the object filename and hash above; `D` refers to the document
filename and hash. Pointers start at the source file's top-level array. Exact
fragments are retained in the local research `evidence.json`; these coordinates
also recover them directly from the immutable exports.

| Evidence | Source | Record UID | Exact JSON pointer | Design issue |
| --- | --- | --- | --- | --- |
| E01 | O | `co5130` | `/0/creation` | Multiple creation dates, maker forms and place forms |
| E02 | D | `aa110136863` | `/0` | Archive record has different fields and shapes |
| E03 | O | `co5130` | `/0/description` | Catalogue and web descriptions |
| E04 | O | `co5130` | `/0/identifier` | Accession and file numbers |
| E05 | O | `co5130` | `/0/name` | Equal object labels with different source types |
| E06 | O | `co5130` | `/0/creation/maker` | Literal combined maker and distinct authority references |
| E07 | O | `co50981` | `/16/agent/0` | Association with open date and role |
| E08 | O | `co8415029` | `/6/component/0` | Component-specific metric and fractional imperial dimensions |
| E09 | O | `co8415027` | `/1/inscription/0` | Verbatim transcription and line endings |
| E10 | D | `aa110091909` | `/2/@hierarchy` | Archival path includes ordered ancestor and self |
| E11 | O | `co94290` | `/31/place` | Unattributed place placeholder repeated with different roles |
| E12 | O | `co8611399` | `/563/facility/0` | Facility reference and display names |
| E13 | O | `co5130` | `/0/child/2` | Child reference with singleton media object |
| E14 | D | `aa110069368` | `/1/legal` | Record rights prose is not an image licence |
| E15 | D | `aa110067159` | `/212/web` | Reference value can be a network path |
| E16 | O | `co8415027` | `/1/sex` | Misleading source field name |
| E17 | O | `co4290` | `/1005/culture` | Undecoded culture codes |
| E18 | O | `co7197` | `/704/location` | Location history and shelf strings |
| E19 | O | `co5130` | `/0/counts` | Source descendant counts |
| E20 | O | `co5130` | `/0/term/1` | Controlled term with attribution date |
| E21 | O | `co62349` | `/365/creation/date` | Creation dates conflict with separate top-level dates |
| E21b | O | `co62349` | `/365/date` | Conflicting top-level date for E21 |
| E22 | D | `aa110132588` | `/1436/creation/date` | January 1837 with reversed source bounds 1837 to 1801 |
| E23 | D | `aa110148405` | `/318/component/0` | Unlabelled dimensions without units |
| E24 | O | `co5130` | `/0/multimedia/0` | Media rights, credit and multiple renditions |
| E25 | O | `co729` | `/121583/inscription/0/details/0` | Rare transliteration with original transcription |
| E26 | D | `aa110118895` | `/1881/multimedia/0` | PDF media with an original representation |
| E27 | O | `co8618850` | `/68/legal/rights/0` | Record-level copyright statement and holder |
| E28 | D | `aa110002568` | `/251/arrangement` | Archive arrangement is an ordered list |
| E29 | D | `aa110002568` | `/251/accrual` | Archival accrual narrative |
| E30 | O | `co25218` | `/122123/creation/date/1` | Rare narrative explaining conflicting date evidence |
| E31 | O | `co8593504` | `/1237/culture/0` | Culture label alongside opaque code variants elsewhere |
| E32 | O | `co55364` | `/21695/physical/0/#value` | Alternative physical reference slot |
| E33 | D | `aa110066051` | `/9/subject` | Subject agent differs from maker |
| E34 | D | `aa110091909` | `/2/fonds` | Fonds relation retained independently of parent |
| E35 | O | `co8406227` | `/11361/arrangement` | Object arrangement is scalar text |
| E36 | O | `co447713` | `/52/facility` | Explicit empty list differs from an absent key |
| E37 | O | `co88674` | `/2535/creation/date` | BCE/CE text without structured bounds |
| E38 | O | `co66564` | `/15/summary` | Record lacks title key; summary still exists |
| E39 | O | `co8712652` | `/27979/creation/maker` | Explicit empty makers array |

Source attribution: © The Board of Trustees of the Science Museum.
Source: Science Museum Group collection exports. Field excerpts retain their
original wording; explanatory interpretations are this project's additions.
See [data and licensing notes](../../data_spec.md) for source terms.
