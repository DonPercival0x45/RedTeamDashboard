"""Rename Red Team Dashboard → Project X-Ray.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-26

Renames:
  - Table:   engagements → projects
  - Type:    engagement_status → project_status
  - Columns: *.engagement_id → *.project_id
  - FK constraints and indexes that reference the renamed columns
  - FindingPhase enum values → PM-generic names:
      osint     → discovery
      vuln_scan → analysis
      exploit   → execution
      phishing  → outreach
      (general stays)
"""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# Tables whose FK column is named engagement_id (becoming project_id).
_FK_TABLES = [
    "approvals",
    "attachments",
    "authorizations",
    "findings",
    "observations",
    "scope_items",
    "suggestions",
    "tasks",
    "agent_executions",
    "audit_log",
]

# Some tables have an index on engagement_id; naming convention:
# ix_<table>_engagement_id → ix_<table>_project_id
_INDEXED_TABLES = [
    "approvals",
    "attachments",
    "authorizations",
    "findings",
    "observations",
    "scope_items",
    "suggestions",
    "tasks",
    "agent_executions",
]


def upgrade() -> None:
    # ── 1. Rename the engagements table ─────────────────────────────────────
    op.rename_table("engagements", "projects")

    # ── 2. Rename the engagement_status Postgres enum ───────────────────────
    op.execute("ALTER TYPE engagement_status RENAME TO project_status")

    # ── 3. Rename engagement_id → project_id in every child table ───────────
    for table in _FK_TABLES:
        # Drop the index first (if it exists) so we can recreate it with the
        # new name after the column rename.
        if table in _INDEXED_TABLES:
            op.drop_index(
                f"ix_{table}_engagement_id",
                table_name=table,
                if_exists=True,
            )
        op.alter_column(table, "engagement_id", new_column_name="project_id")
        if table in _INDEXED_TABLES:
            op.create_index(
                f"ix_{table}_project_id",
                table,
                ["project_id"],
            )

    # ── 4. Rename FindingPhase enum values ───────────────────────────────────
    phase_renames = [
        ("osint", "discovery"),
        ("vuln_scan", "analysis"),
        ("exploit", "execution"),
        ("phishing", "outreach"),
    ]
    for old_val, new_val in phase_renames:
        op.execute(
            f"ALTER TYPE finding_phase RENAME VALUE '{old_val}' TO '{new_val}'"
        )


def downgrade() -> None:
    # ── 4. Undo FindingPhase renames ─────────────────────────────────────────
    phase_renames = [
        ("discovery", "osint"),
        ("analysis", "vuln_scan"),
        ("execution", "exploit"),
        ("outreach", "phishing"),
    ]
    for old_val, new_val in phase_renames:
        op.execute(
            f"ALTER TYPE finding_phase RENAME VALUE '{old_val}' TO '{new_val}'"
        )

    # ── 3. Rename project_id → engagement_id ────────────────────────────────
    for table in reversed(_FK_TABLES):
        if table in _INDEXED_TABLES:
            op.drop_index(
                f"ix_{table}_project_id",
                table_name=table,
                if_exists=True,
            )
        op.alter_column(table, "project_id", new_column_name="engagement_id")
        if table in _INDEXED_TABLES:
            op.create_index(
                f"ix_{table}_engagement_id",
                table,
                ["engagement_id"],
            )

    # ── 2. Undo enum rename ──────────────────────────────────────────────────
    op.execute("ALTER TYPE project_status RENAME TO engagement_status")

    # ── 1. Rename back ───────────────────────────────────────────────────────
    op.rename_table("projects", "engagements")
