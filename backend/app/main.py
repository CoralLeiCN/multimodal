from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.main import api_router
from app.core.config import ROOT, Settings
from app.core.db import make_engine, migrate
from app.services.embeddings import GeminiEmbeddings, SearchError
from app.services.qdrant_store import VectorStore
from app.services.search import SearchService


def create_app(settings=None, *, vectors=None, embeddings=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application):
        engine = make_engine(settings)
        migrate(settings)
        store = vectors or VectorStore(settings)
        embedder = embeddings or GeminiEmbeddings(settings)
        application.state.search = SearchService(engine, settings, store, embedder)
        yield
        store.close()
        embedder.close()
        engine.dispose()

    application = FastAPI(
        title="Collection Explorer API",
        version="0.1.0",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
        generate_unique_id_function=lambda route: route.name,
    )

    @application.exception_handler(SearchError)
    async def search_error(_request: Request, error: SearchError):
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
    frontend = ROOT / "frontend/dist"
    if frontend.is_dir():
        application.mount(
            "/", StaticFiles(directory=frontend, html=True), name="frontend"
        )
    return application


app = create_app()
