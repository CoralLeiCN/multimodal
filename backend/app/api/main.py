from fastapi import APIRouter

from app.api.routes import images, search, status
from app.schemas import ErrorResponse

api_router = APIRouter(
    responses={
        code: {"model": ErrorResponse}
        for code in (404, 409, 413, 415, 422, 429, 503, 504)
    }
)
api_router.include_router(status.router)
api_router.include_router(images.router)
api_router.include_router(search.router)
