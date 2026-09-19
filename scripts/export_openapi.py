"""Export the API contract without starting databases or external clients."""

import json

from app.core.config import ROOT
from app.main import app

destination = ROOT / "frontend/openapi.json"
destination.write_text(json.dumps(app.openapi(), indent=2) + "\n")
print(f"Exported {destination.relative_to(ROOT)}")
