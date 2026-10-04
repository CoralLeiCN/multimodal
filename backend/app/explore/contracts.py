from pydantic import BaseModel, ConfigDict, Field


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
