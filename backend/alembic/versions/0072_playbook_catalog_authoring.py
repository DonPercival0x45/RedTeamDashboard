"""Add durable playbook catalog metadata and version lineage.

Revision ID: 0072
Revises: 0071
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SYSTEM_SLUGS = (
    "osint-passive-domain",
    "ptes-passive-recon",
    "osint-enrichment",
    "email-exposure-triage",
    "domain-web-surface",
    "host-service-validation",
    "cidr-exposure-survey",
    "mail-dns-posture",
    "scope-hygiene-review",
    "dns-ownership-boundary",
    "dangling-dns-triage",
    "web-security-baseline",
    "cloud-edge-boundary",
)


def upgrade() -> None:
    op.add_column(
        "playbooks",
        sa.Column(
            "category",
            sa.String(length=64),
            nullable=False,
            server_default="other",
        ),
    )
    op.add_column(
        "playbooks",
        sa.Column(
            "applicable_entity_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "playbooks",
        sa.Column(
            "origin",
            sa.String(length=24),
            nullable=False,
            server_default="custom",
        ),
    )
    op.add_column(
        "playbooks",
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "playbooks",
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_playbooks_created_by_users",
        "playbooks",
        "users",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_playbooks_supersedes_playbooks",
        "playbooks",
        "playbooks",
        ["supersedes_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_playbooks_category", "playbooks", ["category"])
    op.create_index("ix_playbooks_origin", "playbooks", ["origin"])
    op.create_index("ix_playbooks_created_by", "playbooks", ["created_by"])

    op.execute(
        """
        UPDATE playbooks
        SET applicable_entity_types = jsonb_build_array(applies_to_asset_class)
        WHERE applicable_entity_types = '[]'::jsonb
        """
    )
    quoted_slugs = ", ".join(f"'{slug}'" for slug in _SYSTEM_SLUGS)
    op.execute(f"UPDATE playbooks SET origin = 'system' WHERE slug IN ({quoted_slugs})")
    op.execute(
        """
        UPDATE playbooks
        SET category = CASE
            WHEN slug IN ('osint-passive-domain', 'ptes-passive-recon', 'osint-enrichment')
                THEN 'discovery'
            WHEN slug = 'domain-web-surface' THEN 'enumeration'
            WHEN slug IN ('email-exposure-triage', 'cidr-exposure-survey')
                THEN 'exposure'
            WHEN slug IN ('mail-dns-posture', 'web-security-baseline')
                THEN 'posture'
            WHEN slug IN ('host-service-validation', 'dangling-dns-triage')
                THEN 'validation'
            WHEN slug IN ('scope-hygiene-review', 'dns-ownership-boundary', 'cloud-edge-boundary')
                THEN 'scope_review'
            ELSE 'other'
        END
        """
    )


def downgrade() -> None:
    op.drop_index("ix_playbooks_created_by", table_name="playbooks")
    op.drop_index("ix_playbooks_origin", table_name="playbooks")
    op.drop_index("ix_playbooks_category", table_name="playbooks")
    op.drop_constraint("fk_playbooks_supersedes_playbooks", "playbooks", type_="foreignkey")
    op.drop_constraint("fk_playbooks_created_by_users", "playbooks", type_="foreignkey")
    op.drop_column("playbooks", "supersedes_id")
    op.drop_column("playbooks", "created_by")
    op.drop_column("playbooks", "origin")
    op.drop_column("playbooks", "applicable_entity_types")
    op.drop_column("playbooks", "category")
