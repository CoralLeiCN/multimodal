from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BrandInput(Contract):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=6000)
    colors: str = Field(default="", max_length=500)
    personality: str = Field(default="", max_length=1000)
    typography: str = Field(default="", max_length=1000)
    illustration_style: str = Field(default="", max_length=2000)
    # Retained for existing API clients and historical brand versions.
    preserve: str = Field(default="", max_length=2000)
    avoid: str = Field(default="", max_length=2000)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=3)


class RunInput(Contract):
    brand_version: str
    prompt: str = Field(min_length=1, max_length=6000)
    subject_asset_ids: list[str] = Field(default_factory=list, max_length=1)
    aspect_ratio: Literal["1:1", "4:3", "3:4", "16:9", "9:16"] = "1:1"
    candidate_count: int = Field(default=2, ge=1, le=2)
    subject_strength: Literal["low", "medium", "high"] = "high"
    style_strength: Literal["low", "medium", "high"] = "medium"
    parent_run_id: str | None = None
    selected_asset_id: str | None = None

    @field_validator("subject_asset_ids")
    @classmethod
    def unique_subjects(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Duplicate subjects")
        return value


class Plan(Contract):
    summary: str = Field(max_length=500)
    prompt: str = Field(min_length=1, max_length=6000)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=3)
    question: str | None = Field(default=None, max_length=500)


class Evaluation(Contract):
    subject_score: int = Field(ge=0, le=100)
    brand_score: int = Field(ge=0, le=100)
    request_score: int = Field(ge=0, le=100)
    summary: str = Field(max_length=700)
    action: Literal["accept", "revise"]
    revision_prompt: str = Field(default="", max_length=6000)


class ToolRequest(Contract):
    step_id: str = Field(pattern=r"^[a-z0-9_-]{1,80}$")
    operation: Literal[
        "plan",
        "generate",
        "evaluate",
        "finish",
        "wait",
        "execution_image",
        "execution_finish",
    ]
    arguments: dict = Field(default_factory=dict)


class GenerateInput(Contract):
    aspect_ratio: Literal["1:1", "4:3", "3:4", "16:9", "9:16"] | None = None
    prompt: str = Field(min_length=1, max_length=6000)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=3)
    revision_asset_id: str | None = None


class EvaluateInput(Contract):
    asset_id: str


class FinishInput(Contract):
    asset_ids: list[str] = Field(min_length=1, max_length=3)


class WaitInput(Contract):
    question: str = Field(min_length=1, max_length=500)


class ConversationInput(Contract):
    brand_version: str
    title: str = Field(default="New conversation", min_length=1, max_length=120)


class MessageInput(Contract):
    content: str = Field(min_length=1, max_length=6000)
    subject_asset_id: str | None = None


class BrandOverrides(Contract):
    colors: str | None = Field(default=None, max_length=500)
    preserve: str | None = Field(default=None, max_length=2000)
    avoid: str | None = Field(default=None, max_length=2000)


class ExecutionRequest(Contract):
    prompt: str = Field(min_length=1, max_length=6000)
    image_id: str | None = Field(default=None, min_length=1, max_length=200)
    asset_id: str | None = Field(default=None, min_length=1, max_length=32)
    aspect_ratio: Literal["1:1", "4:3", "3:4", "16:9", "9:16"] = "1:1"
    overrides: BrandOverrides = Field(default_factory=BrandOverrides)

    @model_validator(mode="after")
    def single_source(self):
        if self.image_id and self.asset_id:
            raise ValueError("Choose one source image")
        return self


class ChatDecision(Contract):
    action: Literal["reply", "execute"]
    message: str = Field(default="", max_length=4000)
    execution: ExecutionRequest | None = None

    @model_validator(mode="after")
    def check_action(self):
        if self.action == "execute" and self.execution is None:
            raise ValueError("Execution requires a task")
        if self.action == "reply" and (not self.message or self.execution is not None):
            raise ValueError("Reply requires text and no execution task")
        return self
