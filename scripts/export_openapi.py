"""Export the API contract without starting databases or external clients."""

import json
import os

# Schema generation imports the app but must not export telemetry.
os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

from app.core.config import ROOT
from app.services.agent.config import AgentSettings
from app.studio import create_app

app = create_app(agent_settings=AgentSettings(_env_file=None, enabled=True))

destination = ROOT / "frontend/openapi.json"
destination.write_text(json.dumps(app.openapi(), indent=2) + "\n")
print(f"Exported {destination.relative_to(ROOT)}")
