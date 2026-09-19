from fastapi import APIRouter

from app.api.deps import SearchDep
from app.schemas import FilterOptions, StatusResponse

router = APIRouter(tags=["status"])


@router.get("/status", response_model=StatusResponse)
def status(service: SearchDep):
    return service.status()


@router.get("/filters", response_model=FilterOptions)
def filter_options(service: SearchDep):
    return service.filter_options()
