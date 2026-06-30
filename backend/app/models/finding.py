from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class Severity(enum.StrEnum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class FindingPhase(enum.StrEnum):
    """Engagement phase a finding belongs to — drives which tab it shows in."""

    osint = "osint"
    vuln_scan = "vuln_scan"
    exploit = "exploit"
    phishing = "phishing"
    general = "general"


class FindingStatus(enum.StrEnum):
    """Validation state. ``osint``-phase findings (passive recon — crt_sh,
    subfinder, whois, dns_lookup, etc.) auto-validate at creation because
    the results are factual. Active enum / vuln_scan / exploit / phishing
    findings start ``pending_validation`` and need analyst sign-off before
    the report includes them.

    ``needs_review`` is reserved for the upcoming confirmation-tool flow:
    when an analyst clicks Validate on a manual-tier finding and the
    backend dispatches a follow-up tool run, a failed/dead-target
    confirmation drops the row here instead of promoting it to
    ``validated``. Today the enum value exists in the schema but no code
    writes it yet.
    """

    pending_validation = "pending_validation"
    validated = "validated"
    rejected = "rejected"
    false_positive = "false_positive"
    needs_review = "needs_review"


def default_status_for_phase(phase: FindingPhase) -> FindingStatus:
    """Status a freshly-created finding should land at, given its phase.

    Passive recon (``osint``) auto-validates because the results are
    factual — a DNS record either exists or doesn't. Active scans,
    imported vuln results, exploit attempts, and phishing pretexts go
    through analyst review before they're report-eligible.
    """
    if phase == FindingPhase.osint:
        return FindingStatus.validated
    return FindingStatus.pending_validation


class Finding(Base, TimestampMixin):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="finding_severity"), default=Severity.info, nullable=False
    )
    summary: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    source_tool: Mapped[str | None] = mapped_column(String(120), index=True)
    target: Mapped[str | None] = mapped_column(String(500), index=True)

    phase: Mapped[FindingPhase] = mapped_column(
        Enum(FindingPhase, name="finding_phase"),
        default=FindingPhase.general,
        nullable=False,
        index=True,
    )
    status: Mapped[FindingStatus] = mapped_column(
        Enum(FindingStatus, name="finding_status"),
        default=FindingStatus.pending_validation,
        nullable=False,
        index=True,
    )
    validated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # When the issue was actually observed in a scan, vs ``created_at``
    # which is when the dashboard ingested it. Optional — manual finds
    # and live OSINT pulls leave it null and the UI falls back to created_at.
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Burp Pro Issue Export <serialNumber>. Stamped by the Burp importer
    # so re-imports of the same XML dedup by (engagement_id, this column).
    # Null for every non-Burp source.
    burp_serial_number: Mapped[str | None] = mapped_column(String(64))
