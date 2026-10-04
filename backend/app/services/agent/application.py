"""Attach creation services to the existing app or the standalone cloud API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import ROOT
from app.core.request_limits import RequestBodyLimitMiddleware
from app.services.agent.config import AgentSettings
from app.services.agent.telemetry import configure


def start(application, settings=None):
    settings = (
        settings
        or getattr(application.state, "agent_settings", None)
        or AgentSettings()
    )
    application.state.agent = None
    if not settings.enabled:
        return
    from app.services.agent.db import engine_for, migrate
    from app.services.agent.service import AgentService

    settings.validate_enabled()
    engine = engine_for(settings)
    try:
        migrate(engine, settings.workspace)
        configure(settings)
        application.state.agent = AgentService(settings, engine)
    except BaseException:
        engine.dispose()
        raise


def stop(application):
    if application.state.agent:
        application.state.agent.engine.dispose()


def install(application, *, max_image_bytes=10 * 1024 * 1024, settings=None):
    settings = settings or AgentSettings()
    application.state.agent_settings = settings
    if settings.enabled:
        from app.api.routes.agent import agent_error, router
        from app.services.agent.storage import AgentError

        application.add_exception_handler(AgentError, agent_error)
        application.include_router(router, prefix="/api/v1")
    else:

        @application.get("/api/v1/agent/status", tags=["agent"])
        def agent_status():
            return {
                "enabled": False,
                "authenticated": False,
                "ready": False,
                "message": "Image creation is not configured yet.",
            }

    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_image_bytes=max_image_bytes,
        upload_paths=("/api/v1/agent/assets",),
    )


def create_agent_app(settings=None):
    @asynccontextmanager
    async def lifespan(application):
        start(application, settings)
        try:
            yield
        finally:
            stop(application)

    application = FastAPI(title="Brand Image Agent", lifespan=lifespan)
    install(application, settings=settings)

    @application.exception_handler(RequestValidationError)
    async def invalid(_request, _error):
        return JSONResponse(
            {
                "code": "invalid_request",
                "message": "Check the required fields, image references, and output options.",
            },
            status_code=422,
        )

    frontend = ROOT / "frontend/dist/studio"
    if frontend.is_dir():

        @application.get("/", include_in_schema=False)
        def home():
            return RedirectResponse("/create")

        @application.get("/create", include_in_schema=False)
        def studio():
            return FileResponse(frontend / "index.html")

        application.mount("/", StaticFiles(directory=frontend), name="frontend")
    return application
