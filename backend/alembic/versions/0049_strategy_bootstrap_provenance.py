"""seed provenance flag for strategy workspace bootstrap

Revision ID: 0049
Revises: 0048

Adds a nullable Boolean ``is_bootstrap`` column to ``work_items``,
``coverage_items``, and ``engagement_objectives`` so seeded-starter rows
created by the accepted-initial-strategy bootstrap can be identified
durably instead of by magic-string title/rationale/reason matching.
Backfills the flag from the same strings the reset path used to match on.

NOTE: PR #163 (entity-duplicate-management, revision 0048) merged to main first,
so this migration chains linearly off 0048. (Earlier, while #163 was still open
in a separate worktree, this was down_revision 0047; reparented on pull.)
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

# The exact seed strings produced by suggestion_router.
# _bootstrap_workspace_from_initial_strategy. Kept here (not imported from app
# code) so the migration is self-contained and frozen at the revision it ships.
_SEED_WORK_RATIONALES = (
    "Seeded from the accepted initial strategy.",
    "Seeded from the accepted initial strategy so high-value findings "
    "are reviewed first.",
)
_SEED_COVERAGE_REASON = "Seeded from accepted initial strategy."
_SEED_OBJECTIVE_TITLES = (
    "Validate highest-risk findings",
    "Confirm scope and coverage",
    "Prepare report-ready evidence",
)


def upgrade() -> None:
    for table in ("work_items", "coverage_items", "engagement_objectives"):
        op.add_column(
            table,
            sa.Column(
                "is_bootstrap",
                sa.Boolean(),
                nullable=True,
                server_default="false",
            ),
        )

    op.execute(
        sa.text(
            "UPDATE work_items SET is_bootstrap = true "
            "WHERE rationale = ANY(:rationales)"
        ).bindparams(sa.bindparam("rationales", value=list(_SEED_WORK_RATIONALES)))
    )
    op.execute(
        sa.text(
            "UPDATE coverage_items SET is_bootstrap = true "
            "WHERE reason = :reason"
        ).bindparams(sa.bindparam("reason", value=_SEED_COVERAGE_REASON))
    )
    op.execute(
        sa.text(
            "UPDATE engagement_objectives SET is_bootstrap = true "
            "WHERE title = ANY(:titles)"
        ).bindparams(sa.bindparam("titles", value=list(_SEED_OBJECTIVE_TITLES)))
    )


def downgrade() -> None:
    for table in ("engagement_objectives", "coverage_items", "work_items"):
        op.drop_column(table, "is_bootstrap")
