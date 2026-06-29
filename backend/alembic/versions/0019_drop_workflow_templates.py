"""drop workflow_templates: feature removed

The Templates tab was retired in v0.5.0 (QoL cleanup). The starter packs
(Network Recon / OSINT Enum / Web App) were rarely consumed and the
Strategic agent now generates better per-finding suggestions than any
hand-curated pack. ``WorkflowTemplate`` model + API + frontend tab are
all gone; this migration drops the orphaned table.

Revision ID: 0019
Revises: 0018
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("workflow_templates")


def downgrade() -> None:
    # Rebuild the structure if a downgrade is run — rows are gone for good
    # since the model is deleted from the codebase.
    op.create_table(
        "workflow_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "steps",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
