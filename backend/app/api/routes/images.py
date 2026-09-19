from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, RedirectResponse

from app.api.deps import FilterDep, SearchDep
from app.schemas import BrowseResponse, ImageRead, SearchResponse, SimilarQuery

router = APIRouter(prefix="/images", tags=["images"])


@router.get("", response_model=BrowseResponse)
def browse_images(
    service: SearchDep,
    filters: FilterDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
    cursor: Annotated[str | None, Query(max_length=1000)] = None,
):
    return service.browse(limit, cursor, filters)


@router.get("/{image_id}", response_model=ImageRead)
def image_detail(image_id: UUID, service: SearchDep):
    return service.image(str(image_id))


@router.get(
    "/{image_id}/file",
    response_class=FileResponse,
    responses={
        307: {"description": "Temporary redirect to a signed private R2 image URL"}
    },
)
def image_file(image_id: UUID, service: SearchDep):
    source, mime = service.image_source(str(image_id))
    if isinstance(source, str):
        return RedirectResponse(
            source, status_code=307, headers={"Cache-Control": "no-store"}
        )
    return FileResponse(
        source, media_type=mime, headers={"Cache-Control": "private, max-age=300"}
    )


@router.post("/{image_id}/similar", response_model=SearchResponse)
def similar_images(
    image_id: UUID, service: SearchDep, query: SimilarQuery | None = None
):
    return service.search(
        similar_id=str(image_id),
        limit=query.limit if query else 24,
        filters=query.filters if query else None,
    )
