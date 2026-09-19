from typing import Annotated

from fastapi import Depends, Query, Request
from pydantic import ValidationError

from app.schemas import MetadataFilters
from app.services.embeddings import SearchError
from app.services.search import SearchService


def search_service(request: Request):
    return request.app.state.search


SearchDep = Annotated[SearchService, Depends(search_service)]


def parse_filters(**values) -> MetadataFilters:
    try:
        return MetadataFilters(**values)
    except ValidationError:
        raise SearchError(
            "Use years from -9999 to 9999 (excluding zero), with the starting year no later than the ending year. Choose up to 20 nonblank places or categories, each at most 300 characters.",
            "invalid_filters",
            422,
        ) from None


def filter_parameters(
    date_from: Annotated[int | None, Query(ge=-9999, le=9999)] = None,
    date_to: Annotated[int | None, Query(ge=-9999, le=9999)] = None,
    place: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
):
    return parse_filters(
        date_from=date_from, date_to=date_to, place=place or [], category=category or []
    )


FilterDep = Annotated[MetadataFilters, Depends(filter_parameters)]
