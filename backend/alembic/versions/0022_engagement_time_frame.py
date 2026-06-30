"""engagement time_frame + start_date + end_date

Adds a Nessus-style scheduling label on each engagement so the analyst can
record whether the engagement is repeatable, point-in-time, point-in-time
continuous, or a custom date window. The orchestrator does not act on this
yet — it's metadata. ``custom`` requires both ``start_date`` and ``end_date``
(enforced at the schema layer, not the DB, because back-fills predate the
constraint).

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


_TIME_FRAME_VALUES = (
    "repeatable",
    "point_in_time_continuous",
    "point_in_time",
    "custom",
)


def upgrade() -> None:
    op.execute(
        "CREATE TYPE engagement_time_frame AS ENUM ("
        + ", ".join(f"'{v}'" for v in _TIME_FRAME_VALUES)
        + ")"
    )
    op.add_column(
        "engagements",
        sa.Column(
            "time_frame",
            sa.Enum(*_TIME_FRAME_VALUES, name="engagement_time_frame", create_type=False),
            nullable=False,
            server_default="point_in_time",
        ),
    )
    op.add_column("engagements", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("engagements", sa.Column("end_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("engagements", "end_date")
    op.drop_column("engagements", "start_date")
    op.drop_column("engagements", "time_frame")
    op.execute("DROP TYPE engagement_time_frame")
