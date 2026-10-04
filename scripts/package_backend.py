"""Copy only the Python modules required by one application edition."""

import argparse
import shutil
from pathlib import Path

COMMON = (
    "__init__.py",
    "main.py",
    "models.py",
    "schemas.py",
    "alembic",
    "core/__init__.py",
    "core/config.py",
    "core/db.py",
    "core/telemetry.py",
    "core/request_limits.py",
    "api/__init__.py",
    "api/main.py",
    "api/deps.py",
    "api/routes/__init__.py",
    "api/routes/images.py",
    "api/routes/search.py",
    "api/routes/status.py",
    "services/__init__.py",
    "services/embeddings.py",
    "services/deadline.py",
    "services/siglip_embeddings.py",
    "services/search.py",
    "services/catalogue.py",
    "services/neon_http.py",
    "services/qdrant_store.py",
    "services/selection.py",
    "services/metadata.py",
)
EXTRA = {
    "search": (),
    "web": ("web.py", "explore"),
    "studio": (
        "studio.py",
        "space.py",
        "agent_models.py",
        "agent_runtime",
        "agent_alembic",
        "prompts",
        "services/agent",
        "api/routes/agent.py",
    ),
}


def package(source, target, edition):
    target.mkdir(parents=True, exist_ok=False)
    for name in (*COMMON, *EXTRA[edition]):
        origin, destination = source / name, target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_dir():
            shutil.copytree(
                origin,
                destination,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            shutil.copy2(origin, destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", choices=EXTRA, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(
        Path(__file__).resolve().parents[1] / "backend/app", args.output, args.edition
    )
