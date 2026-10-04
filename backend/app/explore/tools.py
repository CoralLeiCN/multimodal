"""Validated collection tools; credentials and files stay in the trusted API."""

import base64
import io
from uuid import UUID

from PIL import Image, ImageOps
from pydantic import Field

from app.explore.contracts import Contract
from app.schemas import MetadataFilters
from app.services.embeddings import SearchError

TOOL_NAMES = (
    "search_text",
    "search_image",
    "find_similar",
    "get_filter_options",
    "get_image_details",
    "lookup_record",
)


class TextSearch(Contract):
    query: str = Field(min_length=1, max_length=1000)
    filters: MetadataFilters = Field(default_factory=MetadataFilters)
    limit: int = Field(default=12, ge=1, le=24)


class ImageSearch(Contract):
    upload_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    filters: MetadataFilters = Field(default_factory=MetadataFilters)
    limit: int = Field(default=12, ge=1, le=24)


class Similar(Contract):
    image_id: UUID
    filters: MetadataFilters = Field(default_factory=MetadataFilters)
    limit: int = Field(default=12, ge=1, le=24)


class Details(Contract):
    image_id: UUID


class Record(Contract):
    record_uid: str = Field(pattern=r"^(co|aa)[0-9]+$", max_length=100)


def preview(data):
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((512, 512))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=80)
    return {
        "mimeType": "image/jpeg",
        "data": base64.b64encode(output.getvalue()).decode(),
    }


def call(search, name, arguments, read_upload):
    if name == "search_text":
        args = TextSearch.model_validate(arguments)
        return {
            "result": search.search(
                text=args.query, limit=args.limit, filters=args.filters
            ).model_dump(mode="json")
        }
    if name == "search_image":
        args = ImageSearch.model_validate(arguments)
        data, mime = read_upload(args.upload_id)
        return {
            "result": search.search(
                image=data, mime_type=mime, limit=args.limit, filters=args.filters
            ).model_dump(mode="json")
        }
    if name == "find_similar":
        args = Similar.model_validate(arguments)
        return {
            "result": search.search(
                similar_id=str(args.image_id), limit=args.limit, filters=args.filters
            ).model_dump(mode="json")
        }
    if name == "get_filter_options":
        Contract.model_validate(arguments)
        return {"result": search.filter_options().model_dump(mode="json")}
    if name == "get_image_details":
        args = Details.model_validate(arguments)
        generation = search.generation()
        image = search.read_images(generation, [str(args.image_id)]).get(
            str(args.image_id)
        )
        if not image:
            raise SearchError(
                "Image is not in the current catalogue.", "image_missing", 404
            )
        path, _ = search.image_source(str(args.image_id))
        if search.generation().id != generation.id:
            raise SearchError(
                "The catalogue changed. Retry the lookup.", "index_changed", 409
            )
        with path.open("rb") as source:
            data = source.read(search.settings.max_image_bytes + 1)
        if len(data) > search.settings.max_image_bytes:
            raise SearchError("Image is too large.", "image_too_large", 413)
        return {
            "result": {
                "index_version": generation.id,
                "results": [image.model_dump(mode="json")],
            },
            "preview": preview(data),
        }
    if name == "lookup_record":
        args = Record.model_validate(arguments)
        return {"result": search.lookup_record(args.record_uid)}
    raise SearchError("Unknown collection tool.", "invalid_tool", 422)
