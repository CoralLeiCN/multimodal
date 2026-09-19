from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env", ROOT / ".env.local"), extra="ignore"
    )

    gemini_api_key: SecretStr | None = None
    logfire_token: SecretStr | None = None
    logfire_indexer_token: SecretStr | None = None
    logfire_send_to_logfire: bool | Literal["if-token-present"] = "if-token-present"
    logfire_environment: str = "development"
    embedding_model: str = "gemini-embedding-2"
    embedding_dimensions: int = 1536
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection_prefix: str = Field(
        default="smg_images", pattern=r"^[A-Za-z0-9_-]{1,80}$"
    )
    qdrant_collection_name: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$"
    )
    qdrant_api_key: SecretStr | None = None
    search_data_dir: Path = ROOT / "data/search"
    catalogue_database_url: SecretStr | None = Field(default=None, alias="DATABASE_URL")
    direct_database_url: SecretStr | None = Field(
        default=None, alias="DATABASE_URL_UNPOOLED"
    )
    image_root: Path = ROOT / "data/images"
    r2_endpoint_url: str | None = None
    r2_bucket: str | None = None
    r2_access_key_id: SecretStr | None = None
    r2_secret_access_key: SecretStr | None = None
    r2_prefix: str = ""
    max_image_bytes: int = 10 * 1024 * 1024
    max_image_pixels: int = 20_000_000

    def absolute(self, path: Path) -> Path:
        return (path if path.is_absolute() else ROOT / path).resolve()

    @property
    def database_url(self) -> str:
        if self.catalogue_database_url:
            return postgres_url(self.catalogue_database_url)
        raise ValueError(
            "DATABASE_URL is required. Connect Neon with bun run neon link."
        )

    @property
    def migration_database_url(self) -> str:
        if self.catalogue_database_url and self.direct_database_url:
            return postgres_url(self.direct_database_url)
        if self.catalogue_database_url:
            from sqlalchemy.engine import make_url

            url = make_url(self.database_url)
            if url.host and url.host.endswith(".neon.tech"):
                return url.set(host=url.host.replace("-pooler.", ".")).render_as_string(
                    hide_password=False
                )
        return self.database_url

    @property
    def data_dir(self) -> Path:
        return self.absolute(self.search_data_dir)

    @property
    def embedding_cache_path(self) -> Path:
        return self.data_dir / "embedding_cache.sqlite3"

    @property
    def config_hash(self) -> str:
        import hashlib

        value = f"{self.embedding_model}:{self.embedding_dimensions}:original-image-v1"
        return hashlib.sha256(value.encode()).hexdigest()


def postgres_url(value: SecretStr) -> str:
    from sqlalchemy.engine import make_url

    url = make_url(value.get_secret_value())
    if url.drivername not in ("postgres", "postgresql", "postgresql+psycopg"):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection URL.")
    return url.set(drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    )
