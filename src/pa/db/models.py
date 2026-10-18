"""Durable application records.

Identity/auth, jobs, email domain, memories, approvals, observability, audit.
Every user-owned table carries an explicit ``user_id`` so isolation is
enforced at the repository layer (never by trusting a request body).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from pa.db.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="queued")
    request: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result: Mapped[str] = mapped_column(Text, nullable=True)
    error_code: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id"), nullable=False, index=True
    )
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(128), nullable=True, index=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# --- Identity & auth -------------------------------------------------------


class User(Base):
    """A person using PA. ``id`` is the stable user_id referenced everywhere."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Session(Base):
    """Opaque bearer token. Only the SHA-256 hash is stored, so a DB leak
    cannot be replayed as a live session."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Permission(Base):
    """Per-user, per-scope permissions. Deny-by-default when absent."""

    __tablename__ = "permissions"
    __table_args__ = (Index("ix_permissions_user_key", "user_id", "key", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# --- Email domain ----------------------------------------------------------


class EmailMessage(Base):
    """A cached inbound email plus its triage output."""

    __tablename__ = "email_messages"
    __table_args__ = (
        Index("ix_email_user_provider_msg", "user_id", "provider_message_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    from_addr: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    to_addr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Triage output (see pa/services/email_triage.py)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal", index=True)
    needs_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    action_items: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    deadlines: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    triage_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailDraft(Base):
    """A user-reviewable reply. Sends only after explicit approval."""

    __tablename__ = "email_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("email_messages.id"), nullable=True, index=True
    )
    in_reply_to: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    to_addr: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


# --- Observability ---------------------------------------------------------


class RunTrace(Base):
    """Per-node execution trace for a LangGraph run: latency and failures."""

    __tablename__ = "run_traces"
    __table_args__ = (Index("ix_traces_user_job", "user_id", "job_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False, default="", index=True)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ToolCall(Base):
    """Every tool invocation: arguments (sanitized), outcome, latency."""

    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_toolcalls_user_job", "user_id", "job_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    args: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
