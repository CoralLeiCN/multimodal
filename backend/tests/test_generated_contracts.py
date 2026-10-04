import json

from app.core.config import ROOT, Settings
from app.explore.config import ExplorerSettings
from app.web import create_app

from scripts.export_openapi import schema


def test_checked_in_schema_covers_public_web_response_contracts():
    exported = schema()
    assert json.loads((ROOT / "frontend/openapi.json").read_text()) == exported
    web = create_app(
        Settings(_env_file=None), explorer_settings=ExplorerSettings(_env_file=None)
    ).openapi()
    for path, operations in web["paths"].items():
        if not path.startswith("/api/v1/explorer/"):
            continue
        assert exported["paths"][path] == operations
    for name, contract in web["components"]["schemas"].items():
        assert exported["components"]["schemas"][name] == contract
    assert not any("/internal/" in path for path in exported["paths"])
