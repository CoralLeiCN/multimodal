import re
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import ROOT


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env", ROOT / ".env.local"),
        env_prefix="AGENT_",
        extra="ignore",
        populate_by_name=True,
        str_strip_whitespace=True,
    )
    enabled: bool = False
    environment: str = "development"
    access_token: SecretStr | None = None
    workspace: str = "default"
    database_url: str = "sqlite:///data/agent/agent.sqlite3"
    storage: str = "local"
    asset_root: Path = ROOT / "data/agent/assets"
    hf_bucket: str = ""
    hf_token: SecretStr | None = None
    gateway_url: str = "http://127.0.0.1:7860"
    chat_model: str = ""
    collection_api_url: str = ""
    collection_api_token: SecretStr | None = None
    collection_database: Path | None = None
    collection_image_root: Path = Field(
        default=ROOT / "data/images",
        validation_alias=AliasChoices("AGENT_COLLECTION_IMAGE_ROOT", "IMAGE_ROOT"),
    )
    model: str = ""
    evaluation_model: str = ""
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("AGENT_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    )
    image_model: str = "gemini-3.1-flash-image"
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias="GEMINI_API_KEY"
    )
    max_image_bytes: int = 10 * 1024 * 1024
    max_image_pixels: int = 20_000_000
    run_timeout: int = Field(default=600, ge=60, le=3600)
    startup_timeout: int = Field(default=180, ge=30, le=600)
    max_queued_runs: int = Field(default=10, ge=1, le=100)
    logfire_token: SecretStr | None = Field(
        default=None, validation_alias="LOGFIRE_TOKEN"
    )
    logfire_api_url: str = "https://logfire-api.pydantic.dev"

    def validate_enabled(self):
        if not self.access_token or len(self.access_token.get_secret_value()) < 32:
            raise ValueError("AGENT_ACCESS_TOKEN must contain at least 32 characters")
        if self.storage not in {"local", "hf"}:
            raise ValueError("AGENT_STORAGE must be local or hf")
        if self.storage == "hf":
            if not re.fullmatch(r"[\w-]+/[\w-]+", self.hf_bucket, re.ASCII):
                raise ValueError("AGENT_HF_BUCKET must be namespace/bucket-name")
            if not self.hf_token or not self.hf_token.get_secret_value():
                raise ValueError("AGENT_HF_TOKEN is required")
        if self.environment == "production":
            if not self.database_url.startswith("postgresql") or self.storage != "hf":
                raise ValueError("Production requires PostgreSQL and HF bucket storage")
            if not self.logfire_token:
                raise ValueError("Production requires LOGFIRE_TOKEN")
            if self.blockers():
                raise ValueError("Production agent configuration is incomplete")

    def harness_blockers(self):
        missing = []
        if find_spec("openai") is None:
            missing.append("Install the backend agent dependency extra")
        key = self.openai_api_key.get_secret_value() if self.openai_api_key else ""
        if not key or key == "your-key-here":
            missing.append("OpenAI-compatible API key")
        if not self.model.strip():
            missing.append("AGENT_MODEL")
        url = urlparse(self.openai_base_url)
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or not (
                url.scheme == "https"
                or (
                    url.scheme == "http"
                    and url.hostname in {"127.0.0.1", "localhost", "::1"}
                )
            )
        ):
            missing.append("HTTPS or HTTP loopback OpenAI API base URL")
        return missing

    def blockers(self):
        missing = self.harness_blockers()
        key = self.gemini_api_key.get_secret_value() if self.gemini_api_key else ""
        if not key or key == "your-key-here":
            missing.append("Gemini image API key")
        if find_spec("google.genai") is None:
            missing.append("Install the backend agent dependency extra for images")
        url = urlparse(self.gateway_url)
        if (
            not (
                url.scheme == "https"
                or (
                    url.scheme == "http"
                    and url.hostname in {"127.0.0.1", "localhost", "::1"}
                )
            )
            or not url.hostname
            or url.username
            or url.query
            or url.fragment
            or url.path.rstrip("/")
        ):
            missing.append("HTTP loopback or HTTPS gateway origin")
        return missing
