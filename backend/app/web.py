"""Web edition: search plus authenticated Codex collection conversations."""

import asyncio
from contextlib import asynccontextmanager

from fastapi.staticfiles import StaticFiles

from app.core.config import ROOT, Settings
from app.explore.concurrency import blocking
from app.explore.config import ExplorerSettings
from app.explore.routes import router
from app.explore.service import Explorer
from app.main import create_app as create_search_app


def create_app(
    settings=None, *, explorer_settings=None, vectors=None, embeddings=None, runner=None
):
    settings = settings or Settings()
    if settings.catalogue_transport != "postgres":
        raise ValueError(
            "The web edition requires CATALOGUE_TRANSPORT=postgres for "
            "conversation transactions and the supervisor lock."
        )
    options = explorer_settings or ExplorerSettings()

    @asynccontextmanager
    async def extension(app):
        service = Explorer(app.state.search, options, runner=runner)
        try:
            await blocking(service.start)
            service.lock_watch = asyncio.create_task(service.watch_lock())
            app.state.explorer = service
            yield
        finally:
            await service.close()

    app = create_search_app(
        settings,
        vectors=vectors,
        embeddings=embeddings,
        extension=extension,
        upload_paths=("/api/v1/explorer/uploads",),
        frontend=ROOT / "frontend/not-built",
    )
    app.include_router(router, prefix="/api/v1")
    frontend = ROOT / "frontend/dist/web"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
