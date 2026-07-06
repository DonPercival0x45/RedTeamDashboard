"""v1.4.0: finding.exclusion + correlate agent name

Adds analyst-driven "reportability" marking to findings, orthogonal to
``status``. A finding can be validated (real, worth reporting) but marked
``out_of_scope`` (real but not part of the ROE / client-declared scope) or
``outside_roe`` (real but off-limits per legal/contractual terms). Both
values keep the row visible in the Findings tab (dimmed + badged) so the
analyst still sees what they surfaced; the report exporter honors the
new ``omit_excluded`` flag to drop them from the client deliverable.

Also seeds a ``correlate`` value into ``agent_name`` so the new
CorrelateAgent's ``AgentExecution`` rows land on the Costs tab with the
right label.

Revision ID: 0035
Revises: 0034
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    finding_exclusion = sa.Enum(
        "out_of_scope",
        "outside_roe",
        name="finding_exclusion",
    )
    finding_exclusion.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "findings",
        sa.Column(
            "exclusion",
            sa.Enum(
                "out_of_scope",
                "outside_roe",
                name="finding_exclusion",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_findings_exclusion",
        "findings",
        ["exclusion"],
        unique=False,
    )

    # Add correlate to the shared AgentName enum so CorrelateAgent's
    # executions can land in agent_executions with the right label.
    # Postgres requires ALTER TYPE ... ADD VALUE outside a transaction
    # block, so we commit first.
    op.execute("COMMIT")
    op.execute("ALTER TYPE agent_name ADD VALUE IF NOT EXISTS 'correlate'")


def downgrade() -> None:
    op.drop_index("ix_findings_exclusion", table_name="findings")
    op.drop_column("findings", "exclusion")
    sa.Enum(name="finding_exclusion").drop(op.get_bind(), checkfirst=True)
    # NB: postgres has no ALTER TYPE ... DROP VALUE. The 'correlate'
    # value in agent_name is left in place on downgrade — harmless
    # because no rows will reference it once the agent code is gone.
