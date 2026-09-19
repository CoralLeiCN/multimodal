import unicodedata
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MetadataFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_from: int | None = Field(
        default=None,
        ge=-9999,
        le=9999,
        description="Inclusive starting year; ranges overlap. Negative years are BCE; zero is invalid.",
    )
    date_to: int | None = Field(
        default=None,
        ge=-9999,
        le=9999,
        description="Inclusive ending year; either boundary may be omitted.",
    )
    place: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=20
    )
    category: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=20
    )

    @field_validator("place", "category")
    @classmethod
    def normalize_values(cls, values):
        normalized = sorted(
            {
                " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
                for value in values
            }
        )
        if any(not value for value in normalized):
            raise ValueError("Filter values must not be blank.")
        return normalized

    @model_validator(mode="after")
    def valid_range(self):
        if self.date_from == 0 or self.date_to == 0:
            raise ValueError("Year zero is not supported.")
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise ValueError("The starting year must not exceed the ending year.")
        return self


class FilterOptions(BaseModel):
    index_version: str
    places: list[str]
    categories: list[str]
    date_min: int | None = None
    date_max: int | None = None


class TextQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=24, ge=1, le=100)
    filters: MetadataFilters = Field(default_factory=MetadataFilters)


class SimilarQuery(BaseModel):
    limit: int = Field(default=24, ge=1, le=100)
    filters: MetadataFilters = Field(default_factory=MetadataFilters)


class AssociationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    record_uid: str
    image_uid: str
    title: str
    description: str
    date: str
    places: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    date_ranges: list[dict[str, int]] = Field(default_factory=list)
    maker: str
    catalogue_identifiers: str
    licence: str
    copyright: str
    credit: str
    source_url: str


class ImageRead(BaseModel):
    image_id: str
    title: str
    image_url: str
    width: int
    height: int
    associations: list[AssociationRead]
    score: float | None = None


class BrowseResponse(BaseModel):
    index_version: str
    indexed_images: int
    matching_images: int
    items: list[ImageRead]
    next_cursor: str | None = None


class SearchResponse(BaseModel):
    index_version: str
    indexed_images: int
    duration_ms: int
    results: list[ImageRead]


class StatusResponse(BaseModel):
    status: str
    index_version: str | None = None
    indexed_images: int = 0
    sample: bool = True
    search_available: bool = False
    text_search_available: bool = False
    message: str


class ErrorResponse(BaseModel):
    code: str
    message: str
