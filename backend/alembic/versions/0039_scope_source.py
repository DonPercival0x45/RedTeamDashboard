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

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0039"
down_revision = "0038"
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
