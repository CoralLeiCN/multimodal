import time
from uuid import uuid4

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return uuid4().hex


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "explorer_conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(120))
    thread_id: Mapped[str | None] = mapped_column(String(100))
    active_run: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Run(Base):
    __tablename__ = "explorer_runs"
    __table_args__ = (UniqueConstraint("conversation_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("explorer_conversations.id"), index=True
    )
    request_key: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    upload_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    answer: Mapped[str] = mapped_column(Text, default="")
    results: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(100))
    index_version: Mapped[str | None] = mapped_column(String(100))
    deadline: Mapped[float | None] = mapped_column(Float)
    token_hash: Mapped[str | None] = mapped_column(String(64))
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Event(Base):
    __tablename__ = "explorer_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("explorer_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON)


class Upload(Base):
    __tablename__ = "explorer_uploads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    mime: Mapped[str] = mapped_column(String(50))
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
