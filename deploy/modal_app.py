"""Deploy with: uv run --package multimodal-backend modal deploy deploy/modal_app.py."""

from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
app = modal.App("multimodal-agent")
service_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(str(ROOT / "deploy/service-requirements.txt"))
    .add_local_dir(
        str(ROOT / "backend"),
        "/srv/backend",
        copy=True,
        ignore=["tests/**", "__pycache__/**"],
    )
    .add_local_dir(str(ROOT / "deploy"), "/srv/deploy", copy=True)
    .add_local_dir(str(ROOT / "frontend/dist"), "/srv/frontend/dist", copy=True)
    .env(
        {
            "PYTHONPATH": "/srv/backend",
            "AGENT_ENABLED": "true",
            "AGENT_ENVIRONMENT": "production",
        }
    )
    .workdir("/srv")
)
# Provision this Secret with the documented configuration before deployment.
credentials = modal.Secret.from_name("multimodal-agent-services")


@app.function(image=service_image, secrets=[credentials], timeout=300)
@modal.asgi_app()
def web():
    from app.services.agent.application import create_agent_app

    # Cloud creation does not start the collection database or Qdrant.
    return create_agent_app()


@app.function(
    image=service_image,
    secrets=[credentials],
    schedule=modal.Period(seconds=30),
    timeout=300,
    max_containers=1,
)
def worker():
    from app.core.telemetry import configure
    from app.services.agent.config import AgentSettings
    from app.services.agent.db import engine_for, migrate
    from app.services.agent.modal_sandbox import ModalSandboxManager
    from app.services.agent.service import AgentService
    from app.services.agent.worker import Worker

    settings = AgentSettings()
    settings.validate_enabled()
    configure(settings)
    engine = engine_for(settings)
    try:
        migrate(engine, settings.workspace)
        Worker(AgentService(settings, engine), ModalSandboxManager(settings)).tick()
    finally:
        engine.dispose()
