from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import ImageRead


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NewConversation(Contract):
    title: str = Field(default="Collection exploration", min_length=1, max_length=120)


class Message(Contract):
    content: str = Field(min_length=1, max_length=6000)
    upload_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class Answer(Contract):
    answer: str = Field(min_length=1, max_length=12000)
    image_ids: list[str]


class ToolCall(Contract):
    name: str
    arguments: dict = Field(default_factory=dict)


class ExplorerStatus(BaseModel):
    authenticated: bool
    ready: bool
    auth_ready: bool


class ExplorerConversation(BaseModel):
    id: str
    title: str


class ExplorerImage(ImageRead):
    index_version: str


class ExplorerRun(BaseModel):
    id: str
    content: str
    upload_id: str | None
    status: Literal[
        "queued", "running", "succeeded", "failed", "cancelled", "timed_out"
    ]
    answer: str
    results: list[ExplorerImage]
    error: str | None
    usage: dict[str, int]


class ExplorerHistory(ExplorerConversation):
    runs: list[ExplorerRun]


class ExplorerUpload(BaseModel):
    id: str


class ExplorerCancellation(BaseModel):
    status: Literal["cancelled"]


class ExplorerLogout(BaseModel):
    signed_out: bool
