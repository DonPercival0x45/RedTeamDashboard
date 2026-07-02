"""v1.1.0 roadmap_suggestions: implemented_at + implemented_by_user_id

Orthogonal-to-``status`` completion markers for the "Mark completed"
button on ``/settings/feedback``. When an approved suggestion ships, an
admin stamps these two fields — the row stays ``approved`` (so the audit
trail of the approval decision is preserved) but the renderer moves it
from the Open section to the Shipped section of ``ROADMAP.md``.

- ``implemented_at TIMESTAMPTZ`` (nullable, indexed) — when the work
  shipped. NULL = not yet done.
- ``implemented_by_user_id UUID`` (nullable, FK to ``users.id`` ON DELETE
  SET NULL) — the admin who marked it. Provenance only.

Also seeds ``author_user_id`` provenance on the DB side by matching
priority's ``ON DELETE SET NULL`` behavior so a deleted admin doesn't
orphan the row.

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roadmap_suggestions",
        sa.Column(
            "implemented_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "roadmap_suggestions",
        sa.Column(
            "implemented_by_user_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_roadmap_suggestions_implemented_by_user_id",
        "roadmap_suggestions",
        "users",
        ["implemented_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_roadmap_suggestions_implemented_at",
        "roadmap_suggestions",
        ["implemented_at"],
        postgresql_where=sa.text("implemented_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_roadmap_suggestions_implemented_at",
        table_name="roadmap_suggestions",
    )
    op.drop_constraint(
        "fk_roadmap_suggestions_implemented_by_user_id",
        "roadmap_suggestions",
        type_="foreignkey",
    )
    op.drop_column("roadmap_suggestions", "implemented_by_user_id")
    op.drop_column("roadmap_suggestions", "implemented_at")
