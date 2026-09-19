from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import ROOT


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_prefix="AGENT_", extra="ignore"
    )
    enabled: bool = False
    environment: str = "development"
    access_token: SecretStr | None = None
    workspace: str = "default"
    database_url: str = "sqlite:///data/agent/agent.sqlite3"
    storage: str = "local"
    asset_root: Path = ROOT / "data/agent/assets"
    s3_bucket: str = ""
    s3_endpoint: str | None = None
    s3_region: str = "eu-west-2"
    gateway_url: str = ""
    modal_app: str = "multimodal-agent"
    chat_model: str = "gemini-3.8-flash"
    collection_api_url: str = ""
    collection_api_token: SecretStr | None = None
    collection_database: Path = ROOT / "data/search/catalog.sqlite3"
    collection_image_root: Path = ROOT / "data/images"
    model: str = "gemini-3.8-flash"
    evaluation_model: str = "gemini-3.8-flash"
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
        if self.storage not in {"local", "s3"}:
            raise ValueError("AGENT_STORAGE must be local or s3")
        if self.storage == "s3" and not self.s3_bucket:
            raise ValueError("AGENT_S3_BUCKET is required")
        if self.environment == "production":
            if not self.database_url.startswith("postgresql") or self.storage != "s3":
                raise ValueError("Production requires PostgreSQL and S3 storage")
            if not self.logfire_token:
                raise ValueError("Production requires LOGFIRE_TOKEN")
            if self.blockers():
                raise ValueError("Production agent configuration is incomplete")

    def blockers(self):
        missing = []
        key = self.gemini_api_key.get_secret_value() if self.gemini_api_key else ""
        if not key or key == "your-key-here":
            missing.append("Gemini API key")
        url = urlparse(self.gateway_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.query
            or url.fragment
            or url.path.rstrip("/")
        ):
            missing.append("Public HTTPS gateway origin")
        return missing
