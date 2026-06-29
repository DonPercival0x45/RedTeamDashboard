"""users.role RBAC: admin / user / guest

v0.5.0 QoL — replaces the boolean ``is_admin`` flag with a three-tier
role. Existing rows with ``is_admin=true`` migrate to ``role='admin'``;
everyone else lands as ``role='user'`` (the lenient default — new Entra
sign-ins also start there). Admins demote to ``guest`` via SQL today;
a /users admin UI is queued for a future minor.

Permission matrix:
  admin  — full access (settings, integrations, hard-delete, approve feedback)
  user   — start/stop engagements, submit feedback, run agents
  guest  — view-only (no mutations)

Revision ID: 0021
Revises: 0020
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE user_role AS ENUM ('admin', 'user', 'guest')")
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.Enum("admin", "user", "guest", name="user_role"),
            nullable=False,
            server_default="user",
        ),
    )
    # Carry the old is_admin flag forward into the new column.
    op.execute("UPDATE users SET role = 'admin' WHERE is_admin = TRUE")
    op.drop_column("users", "is_admin")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute("UPDATE users SET is_admin = TRUE WHERE role = 'admin'")
    op.drop_column("users", "role")
    op.execute("DROP TYPE IF EXISTS user_role")
