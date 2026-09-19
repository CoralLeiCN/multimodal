"""Separate metadata keeps agent migrations independent of the search catalogue."""

import time
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "agent_workspaces"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)


class Brand(Base):
    __tablename__ = "agent_brand_versions"
    __table_args__ = (UniqueConstraint("workspace", "brand_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace: Mapped[str] = mapped_column(String(100), index=True)
    brand_id: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    profile: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Asset(Base):
    __tablename__ = "agent_assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace: Mapped[str] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    object_key: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(40))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    source: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Run(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("workspace", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace: Mapped[str] = mapped_column(String(100), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    brand_version: Mapped[str] = mapped_column(ForeignKey("agent_brand_versions.id"))
    request: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(50), default="queued")
    attempt_id: Mapped[str | None] = mapped_column(String(32))
    sandbox_id: Mapped[str | None] = mapped_column(String(100))
    sandbox_name: Mapped[str | None] = mapped_column(String(100))
    image_version: Mapped[str | None] = mapped_column(String(64))
    deadline: Mapped[float | None] = mapped_column(Float)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    traceparent: Mapped[str | None] = mapped_column(String(100))
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    question: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(100))
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class Event(Base):
    __tablename__ = "agent_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(50))
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Call(Base):
    __tablename__ = "agent_provider_calls"
    __table_args__ = (UniqueConstraint("run_id", "step_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    step_id: Mapped[str] = mapped_column(String(80))
    operation: Mapped[str] = mapped_column(String(30))
    is_revision: Mapped[bool] = mapped_column(Boolean, default=False)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="submitted")
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    usage: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Conversation(Base):
    __tablename__ = "agent_conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace: Mapped[str] = mapped_column(String(100), index=True)
    brand_version: Mapped[str] = mapped_column(ForeignKey("agent_brand_versions.id"))
    title: Mapped[str] = mapped_column(String(120))
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    subject_asset_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class Message(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence"),
        UniqueConstraint("conversation_id", "idempotency_key"),
        UniqueConstraint("run_id", "role"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("agent_conversations.id"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class TraceBatch(Base):
    __tablename__ = "agent_trace_batches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
