"""Export the combined public edition contracts without starting services."""

import json
import os

# Schema generation imports the app but must not export telemetry.
os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

from app.core.config import ROOT
from app.explore.routes import router as explorer_router
from app.services.agent.config import AgentSettings
from app.studio import create_app


def schema():
    app = create_app(agent_settings=AgentSettings(_env_file=None, enabled=True))
    # Schema-only union; deployed entry points retain their edition-specific routes.
    app.include_router(explorer_router, prefix="/api/v1")
    return app.openapi()


if __name__ == "__main__":
    destination = ROOT / "frontend/openapi.json"
    destination.write_text(json.dumps(schema(), indent=2) + "\n")
    print(f"Exported {destination.relative_to(ROOT)}")
