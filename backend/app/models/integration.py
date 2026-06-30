"""External-system integrations — generic 3rd-party-app hub (v0.9.0).

Before v0.9 this table was one-row-per-type, unique-by-type, and the
single Discord row handled both feedback notifications AND status
alerts. v0.9 splits the assumption:

- Multiple rows of the same provider type can coexist (two Discord
  webhooks pointing at two different channels, for example).
- A free-form ``type`` VARCHAR replaces the old Postgres enum so a new
  provider ships as a frontend module + provider entry, with no
  schema migration per addition.
- A new ``purpose`` enum routes events to the right row at send time
  (status_notifier picks ``purpose='status_alerts'``; feedback push
  picks ``purpose='feedback'``; the GitHub ROADMAP push picks
  ``purpose='roadmap_push'``).
- ``name`` is the analyst-given label shown on each tile in the
  Integrations tab ("Alerts channel", "Feedback channel"); ``logo_url``
  is used by the "Custom" provider where the admin uploads a square PNG
  as a data URL.

Admin-only surface — managed from ``/settings/integrations``.

Config shape per provider:

- ``discord``: ``{webhook_url, bot_token, channel_id, last_seen_message_id}``
- ``teams``: ``{webhook_url}``  (Adaptive Cards — outbound only)
- ``github_push``: ``{pat_token, owner, repo, branch, path}``
- ``custom``: ``{webhook_url, json_template?, http_headers?}``
"""
from __future__ import annotations

import enum
import uuid
from typing import Any, ClassVar

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid7


class IntegrationType:
    """v0.9.0: not an enum anymore — the model column is a free-form
    VARCHAR so new providers can register without a migration. The
    well-known slugs stay exposed here as class-level constants so the
    pre-v0.9 callers that did ``IntegrationType.discord`` keep working
    without changes.
    """

    discord: ClassVar[str] = "discord"
    teams: ClassVar[str] = "teams"
    github_push: ClassVar[str] = "github_push"
    custom: ClassVar[str] = "custom"

    @classmethod
    def known(cls) -> list[str]:
        return [cls.discord, cls.teams, cls.github_push, cls.custom]


class IntegrationPurpose(enum.StrEnum):
    """What kind of event this integration row should receive.

    The send-event side (``status_notifier``, ``discord_feedback``,
    ``github_push`` POST endpoint) queries by ``purpose=`` rather than
    by ``type=`` so an admin can wire two Discord rows — one for
    ``feedback``, one for ``status_alerts`` — and the right one fires.
    """

    feedback = "feedback"
    status_alerts = "status_alerts"
    roadmap_push = "roadmap_push"
    manual = "manual"


class Integration(Base, TimestampMixin):
    __tablename__ = "integrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    # v0.9: free-form VARCHAR(60) — was a Postgres enum until migration 0028.
    type: Mapped[str] = mapped_column(
        String(60), nullable=False, index=True
    )
    purpose: Mapped[IntegrationPurpose] = mapped_column(
        Enum(IntegrationPurpose, name="integration_purpose"),
        nullable=False,
        default=IntegrationPurpose.manual,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
