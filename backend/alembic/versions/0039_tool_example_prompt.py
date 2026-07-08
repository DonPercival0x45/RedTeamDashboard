"""v1.11.0: Tool.example_prompt for the Scope-tab "Current Tools" panel.

Adds a nullable ``example_prompt`` text column to ``tools``. First-party
tools ship with a curated example (populated by future seed / manifest
work); analyst-uploaded tools can leave it null and the frontend falls
back to a generic "Run <name>" shape.

Nothing depends on the column being populated, so backfill is a no-op —
existing rows stay NULL until an admin edits them or a re-seeded tool
overwrites the row.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tools",
        sa.Column("example_prompt", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tools", "example_prompt")
