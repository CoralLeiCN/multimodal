from contextlib import AsyncExitStack, ExitStack, asynccontextmanager

import logfire
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.main import api_router
from app.core.config import ROOT, Settings
from app.core.db import make_engine
from app.core.request_limits import RequestBodyLimitMiddleware
from app.core.telemetry import configure_telemetry, instrument_api
from app.services.catalogue import Catalogue
from app.services.embeddings import SearchError, create_embeddings
from app.services.neon_http import NeonHttpQueries
from app.services.qdrant_store import VectorStore
from app.services.search import SearchService


def create_app(
    settings=None,
    *,
    vectors=None,
    embeddings=None,
    extension=None,
    frontend=None,
    upload_paths=(),
):
    settings = settings or Settings()
    configure_telemetry(settings, "multimodal-api")

    @asynccontextmanager
    async def lifespan(application):
        with ExitStack() as resources:
            resources.callback(logfire.force_flush, timeout_millis=2000)
            engine, catalogue = None, None
            if settings.catalogue_transport == "neon_http":
                queries = NeonHttpQueries(settings)
                resources.callback(queries.close)
                catalogue = Catalogue(queries)
            else:
                engine = make_engine(settings)
                resources.callback(engine.dispose)
            store = vectors or VectorStore(settings)
            resources.callback(store.close)
            embedder = embeddings or create_embeddings(settings)
            resources.callback(embedder.close)
            application.state.search = SearchService(
                engine, settings, store, embedder, catalogue=catalogue
            )
            resources.callback(application.state.search.close)
            async with AsyncExitStack() as extensions:
                if extension:
                    await extensions.enter_async_context(extension(application))
                yield

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
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_request",
                "message": "Check the query, image ID, result limit (1–100), and filters. Use nonzero years from -9999 to 9999 in ascending order and up to 20 nonblank places or categories.",
            },
        )

    application.include_router(api_router, prefix="/api/v1")
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_image_bytes=settings.max_image_bytes,
        upload_paths=upload_paths,
    )
    instrument_api(application)
    frontend = frontend or ROOT / "frontend/dist/search"
    if frontend.is_dir():
        application.mount(
            "/", StaticFiles(directory=frontend, html=True), name="frontend"
        )
    return application


app = create_app()
