from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    gemini_api_key: SecretStr | None = None
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
    sqlite_path: Path = ROOT / "data/search/catalog.sqlite3"
    image_root: Path = ROOT / "data/images"
    max_image_bytes: int = 10 * 1024 * 1024
    max_image_pixels: int = 20_000_000

    def absolute(self, path: Path) -> Path:
        return (path if path.is_absolute() else ROOT / path).resolve()

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.absolute(self.sqlite_path)}"

    @property
    def data_dir(self) -> Path:
        return self.absolute(self.sqlite_path).parent

    @property
    def embedding_cache_path(self) -> Path:
        path = (self.data_dir / "embedding_cache.sqlite3").resolve()
        catalogue = self.absolute(self.sqlite_path)
        if path == catalogue or (
            path.exists() and catalogue.exists() and path.samefile(catalogue)
        ):
            raise ValueError("The embedding cache must be separate from the catalogue.")
        return path

    @property
    def config_hash(self) -> str:
        import hashlib

        value = f"{self.embedding_model}:{self.embedding_dimensions}:original-image-v1"
        return hashlib.sha256(value.encode()).hexdigest()
