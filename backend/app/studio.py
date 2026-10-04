"""The optional Image Studio prototype, separate from released search editions."""

from contextlib import asynccontextmanager

from fastapi.responses import FileResponse

from app.core.config import ROOT
from app.main import create_app as create_search_app
from app.services.agent import application as agent_application


def create_app(settings=None, *, vectors=None, embeddings=None, agent_settings=None):
    @asynccontextmanager
    async def extension(app):
        agent_application.start(app)
        try:
            yield
        finally:
            agent_application.stop(app)

    # Attach feature routes before mounting static files.
    app = create_search_app(
        settings,
        vectors=vectors,
        embeddings=embeddings,
        extension=extension,
        upload_paths=("/api/v1/agent/assets",),
        frontend=ROOT / "frontend/not-built",
    )
    agent_application.install(app, settings=agent_settings)
    frontend = ROOT / "frontend/dist/studio"
    if frontend.is_dir():
        from fastapi.staticfiles import StaticFiles

        @app.get("/create", include_in_schema=False)
        def studio():
            return FileResponse(frontend / "index.html")

        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
