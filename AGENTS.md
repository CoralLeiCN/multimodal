# Repository Guidelines

## Project Structure & Module Organization

This project develops text and image search for the Science Museum Group collection.

- `cronjob/index_images.py` loads prepared Hugging Face catalogue rows, caches images and builds PostgreSQL/Qdrant indexes.
- `cronjob/refresh_image_metadata.py` copies prepared release metadata into existing indexes.
- `backend/app/services/dataset.py` is the dataset consumer. Keep source interpretation in the separate `smg-datasets` repository.
- `data/` contains ignored dataset/model caches and indexing state. No raw JSON or CSV is an application input.
- `data_spec.md` and `data_processing_spec.md` document consumption and attribution. Follow `docs/writting_rules.md`.

## Build, Test, and Development Commands

Use Python 3.12+ from the repository root.

- `uv sync --locked --all-packages`: install locked dependencies.
- `make preview-index`: inspect a prepared dataset sample.
- `make index LIMIT=50 WORKERS=1 BATCH_SIZE=8`: load and index a sample.
- `make index`: resume saved catalogue rows.
- `uv run --all-packages --extra agent --extra web pytest`: run tests with a disposable `TEST_POSTGRES_URL`.
- `uv run ruff check .`: lint Python.

Set `HF_DATASET_ID` and a full commit `HF_DATASET_REVISION`, or use
`HF_DATASET_PATH` for a local version-3 package. Dataset publishing belongs to
`smg-datasets`; this repository keeps application embedding and indexing jobs.

## Code & Specification Alignment

Keep code and specifications aligned in the same change. When implementation behavior changes, update the affected specifications and documentation, including `data_spec.md`, `README.md`, and `cronjob/README.md` where relevant. When a specification changes the intended behavior, update the corresponding implementation. Before completing the task, check that both describe the same inputs, outputs, defaults, data fields, and filtering rules.

## Shared Collection Retrieval

The HF search edition and web edition consume one published Qdrant collection,
its matching PostgreSQL catalogue generation, and the same HF image release.
Keep retrieval in `SearchService`; Codex collection tools call that service.
Index each dataset release once through the separate indexing command. Do not
create an agent-specific collection or write collection vectors from query paths.
Keep model, revision, dimensions and preprocessing aligned across both editions.
Follow the [shared retrieval contract](docs/product_versions.md#shared-retrieval-contract).

## Testing & Linting Guidelines

Do not write tests for reversible, low-impact changes that mirror the implementation. If you choose to write tests, make sure they are meaningful and necessary to verify the implementation.

Run tests appropriate to the change and complete required checks. Once those pass, broaden or repeat testing only when new changes, failures, or unresolved concerns justify it; otherwise, continue toward completing the task.

For frontend changes that affect visible behavior, use the browser to verify the affected flows when available.

Use pytest for Python backend tests and Ruff for linting. When adding tests, use pytest fixtures and `tmp_path` for temporary metadata and image files. Focus on affected behaviors such as parsing, path ambiguity, missing metadata, output preservation after failures, and CLI exit codes. Run the appropriate pytest tests and Ruff before submitting Python changes. Coverage thresholds are currently unspecified.

## Commit & Pull Request Guidelines

Existing commits use short lowercase action phrases, such as `add gitignore`. Use specific subjects such as `fix ambiguous image matching`. Keep each commit focused. Pull requests should explain the problem, behavior changes, validation commands and results, and relevant issues. Update script documentation when options or manifest columns change.

## Data Handling

Keep downloaded assets and generated reports under the ignored `data/` directory. Preserve source identifiers, licence fields, and stable manifest columns. Consult `data_spec.md` before reusing collection content.

## Collection ID Lookup

When a user asks for images by an SMG collection record ID such as `co25823`,
follow [the collection ID lookup procedure](docs/collection_id_lookup.md).
Use an exact, parameterized PostgreSQL match on `image_associations.record_uid` in
the active ready generation, returning all distinct associated images. Resolve
their generated `image_id` UUIDs before using the image API routes. Report a
missing catalogue separately from an ID with no indexed images. Use this route
for identifier requests; semantic text search is for descriptions.
