from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProcessingReceiptStatus(enum.StrEnum):
    processing = "processing"
    completed = "completed"


class ProcessingReceipt(Base):
    """Durable idempotency receipt for command and event side effects."""

    __tablename__ = "processing_receipts"

    delivery_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str | None] = mapped_column(String(200), index=True)
    # Stable accounting identity for Strategic finding events. Deliberately no
    # FK: the receipt commits this UUID before the execution's visibility commit
    # so crash replay can create/reuse exactly that row.
    agent_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), index=True
    )
    status: Mapped[ProcessingReceiptStatus] = mapped_column(
        Enum(ProcessingReceiptStatus, name="processing_receipt_status"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
