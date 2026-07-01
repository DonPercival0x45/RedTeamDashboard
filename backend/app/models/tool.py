"""Tools tab data model (v0.11.0).

Two tables land together so v0.12.0 (Python invocation runtime) can wire
against them without another migration:

- ``Tool`` — catalog entry. One per registered tool. Manifest stored as
  JSONB; source lives at ``artifact_ref`` (blob path for analyst lane;
  OCI image tag for admin binary lane, added in v0.14.0). ``status``
  gates whether an engagement can invoke it: ``draft`` → ``approved`` →
  ``revoked``.

- ``ToolInvocation`` — one row per run. v0.11.0 does not populate this
  table; it exists so the invocation runtime landing in v0.12.0 is
  code-only.

The three enums (``ToolKind``, ``ToolLane``, ``ToolStatus``) plus
``ToolTaskKind`` mirror the manifest schema exactly so a Pydantic
manifest object is one call from being persisted.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid7


class ToolKind(enum.StrEnum):
    python = "python"
    shell = "shell"
    binary = "binary"


class ToolLane(enum.StrEnum):
    """Trust lane. ``analyst`` lane goes through AST + LLM + admin
    approval; ``admin`` lane skips AST/LLM and relies on admin approval
    alone (used for the binary kind in v0.14.0)."""

    analyst = "analyst"
    admin = "admin"


class ToolStatus(enum.StrEnum):
    draft = "draft"
    approved = "approved"
    revoked = "revoked"


class ToolTaskKind(enum.StrEnum):
    """Charter task-kind gate. Agents can only dispatch ``enum`` and
    ``scan`` tools; ``exploit`` tools are analyst-only regardless of
    lane. Mirrors ``models.task.TaskKind`` but kept separate so the
    tool catalog can grow without perturbing Task."""

    enum = "enum"
    scan = "scan"
    exploit = "exploit"


class ToolInvocationStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    timeout = "timeout"


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    kind: Mapped[ToolKind] = mapped_column(
        Enum(ToolKind, name="tool_kind"), nullable=False, index=True
    )
    lane: Mapped[ToolLane] = mapped_column(
        Enum(ToolLane, name="tool_lane"), nullable=False, index=True
    )
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    task_kind: Mapped[ToolTaskKind] = mapped_column(
        Enum(ToolTaskKind, name="tool_task_kind"), nullable=False
    )
    status: Mapped[ToolStatus] = mapped_column(
        Enum(ToolStatus, name="tool_status"),
        nullable=False,
        default=ToolStatus.draft,
        index=True,
    )
    manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    artifact_ref: Mapped[str | None] = mapped_column(String(500))
    validation: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=datetime.now,
    )

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_tools_name_version"),
    )


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    tool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tools.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    tool_version: Mapped[int] = mapped_column(Integer, nullable=False)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoker_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    args: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    runtime_ref: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[ToolInvocationStatus] = mapped_column(
        Enum(ToolInvocationStatus, name="tool_invocation_status"),
        nullable=False,
        default=ToolInvocationStatus.queued,
        index=True,
    )
    exit_code: Mapped[int | None] = mapped_column(Integer)
    stdout: Mapped[str | None] = mapped_column(Text)
    stderr: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
