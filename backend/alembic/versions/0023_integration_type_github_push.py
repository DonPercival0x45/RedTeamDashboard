"""Add 'github_push' to integration_type enum

v0.6.0 introduces a second integration kind: a one-shot "Push to GitHub"
button on the Feedback page that commits the approved-suggestions
ROADMAP.md to a repo via the GitHub Contents API. Re-uses the existing
``integrations`` row shape (one row per type, ``config`` JSONB carrying
``{pat_token, owner, repo, branch, path}``).

Postgres won't let us drop an enum value, so the downgrade is a no-op —
the value just stays orphaned in the type.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE works inside a transaction on Postgres 12+
    # as long as the new value isn't used in the same transaction. We only
    # add the symbol here; the first row using it lands at runtime.
    op.execute("ALTER TYPE integration_type ADD VALUE IF NOT EXISTS 'github_push'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums short of recreating the type.
    # Leave the symbol in place on downgrade.
    pass
