from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import JSON, Column, ForeignKeyConstraint, MetaData
from sqlmodel import Field, SQLModel


def now() -> str:
    return datetime.now(UTC).isoformat()


class Generation(SQLModel, table=True):
    __tablename__ = "index_generations"
    id: str = Field(primary_key=True)
    collection: str = Field(unique=True)
    status: str = "building"
    model: str
    dimensions: int
    config_hash: str
    count: int = 0
    scanned: int = 0
    skipped: int = 0
    created_at: str = Field(default_factory=now)
    completed_at: str | None = None
    error: str | None = None


class Image(SQLModel, table=True):
    __tablename__ = "images"
    generation_id: str = Field(primary_key=True, foreign_key="index_generations.id")
    image_id: str = Field(primary_key=True)
    location: str
    relative_path: str
    r2_url: str | None = None
    checksum: str
    mime_type: str
    width: int
    height: int
    title: str
    filter_metadata: list[dict] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )


class Association(SQLModel, table=True):
    __tablename__ = "image_associations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["generation_id", "image_id"], ["images.generation_id", "images.image_id"]
        ),
    )
    id: str = Field(primary_key=True)
    generation_id: str = Field(index=True)
    image_id: str = Field(index=True)
    record_uid: str
    image_uid: str = ""
    source_json: str
    title: str = ""
    description: str = ""
    date: str = ""
    places: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    categories: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    date_ranges: list[dict] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    maker: str = ""
    catalogue_identifiers: str = ""
    licence: str = ""
    copyright: str = ""
    credit: str = ""


class Ingestion(SQLModel, table=True):
    __tablename__ = "image_ingestions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["generation_id", "image_id"], ["images.generation_id", "images.image_id"]
        ),
    )
    generation_id: str = Field(primary_key=True)
    image_id: str = Field(primary_key=True)
    checksum: str
    config_hash: str
    point_id: str
    collection: str
    status: str = "pending"
    attempts: int = 0
    last_error: str | None = None
    updated_at: str = Field(default_factory=now)
    indexed_at: str | None = None


class EmbeddingCache(SQLModel, table=True):
    # This table belongs exclusively to the local cache database.
    metadata: ClassVar[MetaData] = MetaData()
    __tablename__ = "embedding_cache"
    key: str = Field(primary_key=True)
    checksum: str
    config_hash: str
    vector: list[float] = Field(sa_column=Column(JSON, nullable=False))
    created_at: str = Field(default_factory=now)


class ServiceState(SQLModel, table=True):
    __tablename__ = "service_state"
    id: int = Field(default=1, primary_key=True)
    active_generation: str = Field(foreign_key="index_generations.id")
