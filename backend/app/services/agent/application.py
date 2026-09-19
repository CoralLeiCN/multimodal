"""Attach creation services to the existing app or the standalone cloud API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.agent import agent_error, router
from app.core.config import ROOT
from app.core.telemetry import configure
from app.services.agent.config import AgentSettings
from app.services.agent.db import engine_for, migrate
from app.services.agent.service import AgentService
from app.services.agent.storage import AgentError


def start(application, settings=None):
    settings = settings or AgentSettings()
    application.state.agent = None
    if not settings.enabled:
        return
    settings.validate_enabled()
    engine = engine_for(settings)
    migrate(engine, settings.workspace)
    configure(settings)
    application.state.agent = AgentService(settings, engine)


def stop(application):
    if application.state.agent:
        application.state.agent.engine.dispose()


def install(application):
    application.add_exception_handler(AgentError, agent_error)
    application.include_router(router, prefix="/api/v1")

    @application.middleware("http")
    async def limit_agent_body(request: Request, call_next):
        if request.url.path.startswith("/api/v1/agent") and request.method in {
            "POST",
            "PUT",
            "PATCH",
        }:
            limit = (
                11 * 1024 * 1024
                if request.url.path == "/api/v1/agent/assets"
                else 256 * 1024
            )
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    return JSONResponse(
                        {
                            "code": "body_too_large",
                            "message": "The upload exceeds the request limit.",
                        },
                        status_code=413,
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        return await call_next(request)


def create_agent_app(settings=None):
    @asynccontextmanager
    async def lifespan(application):
        start(application, settings)
        yield
        stop(application)

    application = FastAPI(title="Brand Image Agent", lifespan=lifespan)
    install(application)

    @application.exception_handler(RequestValidationError)
    async def invalid(_request, _error):
        return JSONResponse(
            {
                "code": "invalid_request",
                "message": "Check the required fields, image references, and output options.",
            },
            status_code=422,
        )

    frontend = ROOT / "frontend/dist"
    if frontend.is_dir():

        @application.get("/", include_in_schema=False)
        def home():
            return RedirectResponse("/create")

        @application.get("/create", include_in_schema=False)
        def studio():
            return FileResponse(frontend / "index.html")

        application.mount("/", StaticFiles(directory=frontend), name="frontend")
    return application
