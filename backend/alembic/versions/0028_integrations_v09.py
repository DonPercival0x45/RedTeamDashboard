"""v0.9.0 Integrations rework: multi-row per type + purpose routing

Reshape the ``integrations`` table from "one row per type, unique-by-type"
into the generic 3rd-party-app hub the Integrations tab needs:

- DROP the unique constraint on ``type`` so multiple rows of the same type
  can coexist (two Discord webhooks for two different channels, etc).
- CONVERT ``type`` from a Postgres enum (``integration_type``) to a
  free-form ``VARCHAR(60)``. New providers ship as a frontend module
  edit only — no migration per addition.
- ADD ``purpose`` (Postgres enum ``integration_purpose``) — routes
  events to the right integration row. v0.9 starts with four values:
  ``feedback`` (the existing roadmap-suggestion notification path),
  ``status_alerts`` (the v0.8 Discord webhook for agent/run failures),
  ``roadmap_push`` (the github_push integration), and ``manual``
  (a catch-all when the admin sets up an integration that isn't
  wired to any auto-event yet).
- ADD ``name VARCHAR(120) NOT NULL`` for the analyst-given label
  shown on the configured-integrations tile ("Alerts channel",
  "Feedback channel").
- ADD ``display_name VARCHAR(120)`` NULLABLE (optional override; falls
  back to provider label + purpose).
- ADD ``logo_url VARCHAR(500)`` NULLABLE — used by the "Custom"
  integration kind where the admin uploads a square logo as a data URL.
  NULL for built-in providers (Discord, Teams, GitHub-push, etc).

Backfill for existing rows:
- The Discord row stays — backfilled with ``purpose='feedback'``,
  ``name='Discord (feedback)'``, ``display_name='Discord — Feedback
  channel'``. This is the row that has been posting feedback
  notifications all along; v0.9 surfaces it on the new Integrations
  tab unchanged.
- The github_push row (if present) backfilled with
  ``purpose='roadmap_push'``, ``name='GitHub ROADMAP Push'``.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


_PURPOSE_VALUES = ("feedback", "status_alerts", "roadmap_push", "manual")


def upgrade() -> None:
    # 1. Purpose enum + column. Default 'manual' so the column can be
    #    added NOT NULL without a backfill blocker; we backfill the
    #    known purposes immediately afterward.
    op.execute(
        "CREATE TYPE integration_purpose AS ENUM ("
        + ", ".join(f"'{v}'" for v in _PURPOSE_VALUES)
        + ")"
    )
    op.add_column(
        "integrations",
        sa.Column(
            "purpose",
            sa.Enum(*_PURPOSE_VALUES, name="integration_purpose", create_type=False),
            nullable=False,
            server_default="manual",
        ),
    )

    # 2. Name + display_name + logo_url. ``name`` carries a default of
    #    empty string so the NOT NULL add succeeds; the backfill below
    #    fills the real value before we drop the default.
    op.add_column(
        "integrations",
        sa.Column("name", sa.String(length=120), nullable=False, server_default=""),
    )
    op.add_column(
        "integrations",
        sa.Column("display_name", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column("logo_url", sa.String(length=500), nullable=True),
    )

    # 3. Backfill known providers. The casts to ::text let us match the
    #    enum value without depending on the enum's identity.
    op.execute(
        """
        UPDATE integrations
        SET purpose      = 'feedback',
            name         = 'Discord (feedback)',
            display_name = 'Discord — Feedback channel'
        WHERE type::text = 'discord'
        """
    )
    op.execute(
        """
        UPDATE integrations
        SET purpose      = 'roadmap_push',
            name         = 'GitHub ROADMAP Push',
            display_name = 'GitHub — ROADMAP push'
        WHERE type::text = 'github_push'
        """
    )

    # 4. Drop the server_default on name now that every row has a real value.
    op.alter_column("integrations", "name", server_default=None)

    # 5. Drop the unique-by-type constraint. Alembic doesn't reliably auto-
    #    detect the constraint name from a unique=True column, so we look
    #    it up first and drop whichever name PostgreSQL gave it.
    op.execute(
        """
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            SELECT con.conname
              INTO constraint_name
              FROM pg_constraint con
              JOIN pg_class rel ON rel.oid = con.conrelid
             WHERE rel.relname = 'integrations'
               AND con.contype = 'u'
             LIMIT 1;
            IF constraint_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE integrations DROP CONSTRAINT %I', constraint_name);
            END IF;
        END;
        $$
        """
    )

    # 6. Convert type column from enum to VARCHAR(60). The USING clause
    #    casts each existing value through ::text so it lands as a plain
    #    string ("discord", "github_push") in the new column.
    op.alter_column(
        "integrations",
        "type",
        existing_type=sa.Enum(name="integration_type"),
        type_=sa.String(length=60),
        existing_nullable=False,
        postgresql_using="type::text",
    )

    # 7. Drop the now-unused integration_type enum.
    op.execute("DROP TYPE integration_type")

    # 8. Indexes for the lookups status_notifier + the roadmap push +
    #    the Integrations tab list view all do.
    op.create_index(
        "ix_integrations_type", "integrations", ["type"], unique=False
    )
    op.create_index(
        "ix_integrations_purpose", "integrations", ["purpose"], unique=False
    )


def downgrade() -> None:
    # Downgrade reverses the column adds but does NOT restore the unique
    # constraint on type — if a tenant ran v0.9 they may now have multi-
    # row Discord setups, and the unique add would fail. Operators who
    # need to roll back must hand-resolve that first.
    op.drop_index("ix_integrations_purpose", table_name="integrations")
    op.drop_index("ix_integrations_type", table_name="integrations")
    op.execute(
        "CREATE TYPE integration_type AS ENUM ('discord', 'github_push')"
    )
    op.alter_column(
        "integrations",
        "type",
        existing_type=sa.String(length=60),
        type_=sa.Enum("discord", "github_push", name="integration_type", create_type=False),
        existing_nullable=False,
        postgresql_using="type::integration_type",
    )
    op.drop_column("integrations", "logo_url")
    op.drop_column("integrations", "display_name")
    op.drop_column("integrations", "name")
    op.drop_column("integrations", "purpose")
    op.execute("DROP TYPE integration_purpose")
