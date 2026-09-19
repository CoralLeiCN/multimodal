# Repository Guidelines

## Project Structure & Module Organization

This project develops text and image search for the Science Museum Group collection.

- `cronjob/match_images.py` streams metadata exports and matches local image paths, producing a CSV manifest and JSON coverage report.
- `cronjob/silver_to_gold.py` converts silver rows with available local images to gold Parquet while preserving their text values and empty fields.
- `cronjob/test_*.py` contains the automated tests; `cronjob/README.md` documents script options and output fields.
- `data/bronze/` holds original JSON exports, `data/silver/` holds official processed CSVs, and `data/gold/` holds generated Parquet files. `data/images/` holds extracted thumbnails. The optional `data/processed/` directory is created on demand for image matching outputs and may be removed after review. Git ignores `data/`.
- `data_view.ipynb` is currently an empty notebook placeholder.
- `data_spec.md` documents data sources and licensing. Follow `docs/writting_rules.md` for documentation style.
- `pyproject.toml` and `uv.lock` define the Python environment.

## Build, Test, and Development Commands

Run commands from the repository root with Python 3.12 or newer. The silver-to-gold converter uses pandas for CSV and Parquet operations, with PyArrow as the Parquet engine. The standalone matching script uses the standard library.

- `uv sync --locked`: install the locked environment, including the development notebook kernel.
- `uv run cronjob/silver_to_gold.py`: convert the official silver CSV into gold Parquet.
- `python3 cronjob/match_images.py`: process bronze exports and images into `data/processed/image_manifest.csv` and `image_match_summary.json`.
- `python3 cronjob/match_images.py --help`: inspect input paths, output paths, and strict matching options.
- `uv run --with pytest pytest`: run the Python backend tests.
- `uv run --with ruff ruff check .`: lint Python code with Ruff.

The `--with` options supply pytest and Ruff until they are added to the project's development dependencies.

## Code & Specification Alignment

Keep code and specifications aligned in the same change. When implementation behavior changes, update the affected specifications and documentation, including `data_spec.md`, `README.md`, and `cronjob/README.md` where relevant. When a specification changes the intended behavior, update the corresponding implementation. Before completing the task, check that both describe the same inputs, outputs, defaults, data fields, and filtering rules.

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
Use an exact, parameterized SQLite match on `image_associations.record_uid` in
the active ready generation, returning all distinct associated images. Resolve
their generated `image_id` UUIDs before using the image API routes. Report a
missing catalogue separately from an ID with no indexed images. Use this route
for identifier requests; semantic text search is for descriptions.
