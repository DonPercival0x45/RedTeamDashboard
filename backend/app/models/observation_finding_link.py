"""Observation ↔ finding link (v1.4.8).

Many-to-many join — an observation can reference the findings it
supports, and the finding slide-over renders the back-references. See
``alembic/versions/0038_observation_findings.py`` for the table. Cascade
both ways; composite PK makes repeated link calls idempotent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ObservationFindingLink(Base):
    __tablename__ = "observation_findings"

    observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("observations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
