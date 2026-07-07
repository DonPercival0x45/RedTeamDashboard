"""v1.4.13: scope_items.source — provenance for "found" scope

Roadmap #5 ("Found Scope"): distinguish scope items the client
provided formally from ones added during the engagement because they
turned up in findings. Per maintainer direction this is NOT a second
field/section — one scope list, with a ``source`` marker the UI tints
green for ``found`` items.

``defined`` (default) = original client-provided scope.
``found``              = added from findings / discovered mid-engagement.

Backfills existing rows to ``defined``. String column (not an enum) so
the vocabulary can grow without a type migration.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# v1.15.0: renumbered 0039 -> 0041 during bundle C cherry-pick. 0039 is
# tool_example_prompt (v1.11.0) and 0040 is user_default_model (v1.13.0);
# down-revision hops to 0040 so the linear chain stays intact.
revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scope_items",
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default="defined",
        ),
    )


def downgrade() -> None:
    op.drop_column("scope_items", "source")
