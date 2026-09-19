from contextlib import asynccontextmanager

import logfire
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.main import api_router
from app.core.config import ROOT, Settings
from app.core.db import make_engine, migrate
from app.core.telemetry import configure_telemetry, instrument_api
from app.services.agent import application as agent_application
from app.services.embeddings import GeminiEmbeddings, SearchError
from app.services.qdrant_store import VectorStore
from app.services.search import SearchService


def create_app(settings=None, *, vectors=None, embeddings=None):
    settings = settings or Settings()
    configure_telemetry(settings, "multimodal-api")

    @asynccontextmanager
    async def lifespan(application):
        engine = make_engine(settings)
        migrate(settings)
        store = vectors or VectorStore(settings)
        embedder = embeddings or GeminiEmbeddings(settings)
        application.state.search = SearchService(engine, settings, store, embedder)
        agent_application.start(application)
        yield
        agent_application.stop(application)
        store.close()
        embedder.close()
        engine.dispose()
        logfire.force_flush(timeout_millis=2000)

    application = FastAPI(
        title="Collection Explorer API",
        version="0.1.0",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
        generate_unique_id_function=lambda route: route.name,
    )

    @application.exception_handler(SearchError)
    async def search_error(_request: Request, error: SearchError):
        logfire.warn(
            "Search request failed", error_code=error.code, status=error.status
        )
        return JSONResponse(
            status_code=error.status,
            content={"code": error.code, "message": str(error)},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError):
        if _request.url.path.startswith("/api/v1/agent"):
            return JSONResponse(
                status_code=422,
                content={
                    "code": "invalid_request",
                    "message": "Check the required fields, image references, and output options.",
                },
            )
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_request",
                "message": "Check the query, image ID, result limit (1–100), and filters. Use nonzero years from -9999 to 9999 in ascending order and up to 20 nonblank places or categories.",
            },
        )

    application.include_router(api_router, prefix="/api/v1")
    agent_application.install(application)
    instrument_api(application)
    frontend = ROOT / "frontend/dist"
    if frontend.is_dir():

        @application.get("/create", include_in_schema=False)
        def creation_page():
            return FileResponse(frontend / "index.html")

        application.mount(
            "/", StaticFiles(directory=frontend, html=True), name="frontend"
        )
    return application


app = create_app()
