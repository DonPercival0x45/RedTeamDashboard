"""Durable worker process, component, and operational-failure telemetry."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class WorkerInstance(Base, TimestampMixin):
    """One worker process boot.

    Health is derived from the heartbeat timestamp instead of persisted as a
    boolean, so an abruptly terminated process naturally becomes stale.
    """

    __tablename__ = "worker_instances"
    __table_args__ = (Index("ix_worker_instances_role_heartbeat", "role", "heartbeat_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deployment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


class WorkerComponent(Base, TimestampMixin):
    """One supervised role or execution lane inside a worker process."""

    __tablename__ = "worker_components"
    __table_args__ = (
        UniqueConstraint(
            "worker_instance_id",
            "name",
            "slot",
            name="uq_worker_component_slot",
        ),
        Index("ix_worker_components_heartbeat", "heartbeat_at"),
        Index("ix_worker_components_current_run", "current_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    worker_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("worker_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    owner_token: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="starting", server_default="starting"
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    restart_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_runs.id", ondelete="SET NULL"),
        nullable=True,
    )


class WorkerOperationalEvent(Base):
    """Append-only, redacted worker infrastructure incident."""

    __tablename__ = "worker_operational_events"
    __table_args__ = (
        Index("ix_worker_events_occurred", "occurred_at"),
        Index("ix_worker_events_severity_occurred", "severity", "occurred_at"),
        Index("ix_worker_events_run", "playbook_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    worker_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("worker_instances.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    component: Mapped[str | None] = mapped_column(String(80), nullable=True)
    slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    engagement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="SET NULL"),
        nullable=True,
    )
    playbook_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
