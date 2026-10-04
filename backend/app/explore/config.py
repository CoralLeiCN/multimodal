from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import ROOT


class ExplorerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EXPLORER_",
        env_file=(ROOT / ".env", ROOT / ".env.local"),
        extra="ignore",
    )
    public_url: str = "http://127.0.0.1:8000"
    gateway_url: str = "http://127.0.0.1:8000"
    session_secret: SecretStr | None = None
    oauth_client_id: str = ""
    oauth_client_secret: SecretStr | None = None
    api_key: SecretStr | None = None
    model: str = ""
    state_dir: Path = ROOT / "data/explorer"
    max_concurrent: int = Field(default=2, ge=1, le=8)
    run_timeout: int = Field(default=180, ge=10, le=600)
    max_tool_calls: int = Field(default=20, ge=1, le=60)
    max_turns: int = Field(default=30, ge=1, le=100)

    @model_validator(mode="after")
    def origins(self):
        for name in ("public_url", "gateway_url"):
            value = getattr(self, name).rstrip("/")
            parsed = urlsplit(value)
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (
                parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path
                or not parsed.hostname
            ):
                raise ValueError(f"{name} must be an origin")
            if name == "gateway_url" and not local:
                raise ValueError("gateway_url must use loopback")
            if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
                raise ValueError(f"{name} requires HTTPS outside loopback")
            setattr(self, name, value)
        return self

    @property
    def ready(self):
        return bool(
            self.api_key
            and self.api_key.get_secret_value().strip()
            and self.model.strip()
        )

    @property
    def auth_ready(self):
        return bool(
            self.session_secret
            and len(self.session_secret.get_secret_value()) >= 32
            and self.oauth_client_id
        )
