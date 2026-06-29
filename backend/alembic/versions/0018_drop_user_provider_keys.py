"""drop user_provider_keys: keys are ephemeral now

Per-analyst BYO keys move to Redis (sliding-TTL hash per user) so they
exist only for the analyst's active session and never at rest in the DB.
This kills two surfaces:

1. Persistent encrypted blobs that any future operator with DB access
   could decrypt (Fernet key lives in the same deployment).
2. The engagement-creator-key reuse path — Strategic/Tactical historically
   resolved the BYO key off ``engagement.created_by``, meaning anyone who
   could trigger a run on someone else's engagement was billing/exposing
   that creator's key. The new model requires the *kicking analyst* to
   have an active Redis-cached key; no cross-user reuse is possible.

Existing rows are dropped — no migration into Redis. Analysts re-upload
on their next sign-in. Memory + the 5qprod deployment notes carry the
operator instruction.

Revision ID: 0018
Revises: 0017
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("user_provider_keys")
    op.execute("DROP TYPE IF EXISTS provider_key_kind")


def downgrade() -> None:
    # Best-effort restore of the schema. Existing keys ARE gone; this only
    # rebuilds the structure so the downgrade doesn't break the migration
    # chain. If you actually need to roll back to persistent keys, you'll
    # also need to flip the resolver / API back over (touches ~6 files).
    provider_key_kind = postgresql.ENUM(
        "model_provider",
        "mcp_server",
        name="provider_key_kind",
    )
    provider_key_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "user_provider_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            provider_key_kind,
            nullable=False,
            server_default="model_provider",
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column(
            "is_local",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "models",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("endpoint", sa.String(2000), nullable=True),
        sa.Column("encrypted_key", sa.LargeBinary(), nullable=True),
        sa.Column("key_last4", sa.String(4), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
        sa.UniqueConstraint("user_id", "name", name="uq_user_provider_keys_user_id_name"),
    )
    op.create_index(
        "ix_user_provider_keys_user_id",
        "user_provider_keys",
        ["user_id"],
    )
