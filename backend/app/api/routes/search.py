from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from app.api.deps import SearchDep, parse_filters
from app.schemas import SearchResponse, TextQuery
from app.services.selection import validate_image

router = APIRouter(prefix="/search", tags=["search"])


@router.post("/text", response_model=SearchResponse)
def search_text(query: TextQuery, service: SearchDep):
    return service.search(text=query.query, limit=query.limit, filters=query.filters)


@router.post("/image", response_model=SearchResponse)
def search_image(
    image: Annotated[UploadFile, File()],
    service: SearchDep,
    limit: Annotated[int, Form(ge=1, le=100)] = 24,
    date_from: Annotated[int | None, Form(ge=-9999, le=9999)] = None,
    date_to: Annotated[int | None, Form(ge=-9999, le=9999)] = None,
    place: Annotated[list[str] | None, Form()] = None,
    category: Annotated[list[str] | None, Form()] = None,
):
    try:
        filters = parse_filters(
            date_from=date_from,
            date_to=date_to,
            place=place or [],
            category=category or [],
        )
        data = image.file.read(service.settings.max_image_bytes + 1)
        mime, _width, _height = validate_image(data, service.settings)
        return service.search(image=data, mime_type=mime, limit=limit, filters=filters)
    finally:
        image.file.close()
