"""engagement memory: memory_elements + memory_links (architecture v3, step 1)

Additive only. New enum types + two tables; no changes to existing tables.
Nothing runs for legacy engagements — Memory rows exist only for v2-flagged
engagements. Reuses the existing ``actor_type`` enum for attribution.
"""
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE memory_kind AS ENUM "
        "('fact', 'hypothesis', 'open_question', 'thread', 'decision')"
    )
    op.execute("CREATE TYPE memory_tier AS ENUM ('hot', 'cold', 'archived')")
    op.execute(
        "CREATE TYPE memory_status AS ENUM "
        "('open', 'resolved', 'dismissed', 'superseded')"
    )
    op.execute(
        "CREATE TYPE memory_link_relation AS ENUM "
        "('supports', 'refutes', 'evidence', 'folds_into', 'supersedes')"
    )
    op.execute(
        "CREATE TYPE memory_link_target_type AS ENUM "
        "('memory_element', 'finding', 'entity')"
    )

    op.execute(
        """
        CREATE TABLE memory_elements (
            id UUID PRIMARY KEY,
            engagement_id UUID NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
            kind memory_kind NOT NULL,
            tier memory_tier NOT NULL DEFAULT 'hot',
            status memory_status NOT NULL DEFAULT 'open',
            summary TEXT NOT NULL,
            body JSONB NOT NULL DEFAULT '{}'::jsonb,
            confidence DOUBLE PRECISION NULL,
            token_estimate INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            author_type actor_type NOT NULL,
            author_id TEXT NOT NULL,
            superseded_by UUID NULL REFERENCES memory_elements(id) ON DELETE SET NULL,
            last_referenced_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_memory_elements_eng_tier ON memory_elements (engagement_id, tier)")
    op.execute("CREATE INDEX ix_memory_elements_eng_kind ON memory_elements (engagement_id, kind)")

    op.execute(
        """
        CREATE TABLE memory_links (
            id UUID PRIMARY KEY,
            from_element_id UUID NOT NULL REFERENCES memory_elements(id) ON DELETE CASCADE,
            relation memory_link_relation NOT NULL,
            target_type memory_link_target_type NOT NULL,
            target_id UUID NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_memory_links_from ON memory_links (from_element_id)")
    op.execute("CREATE INDEX ix_memory_links_target ON memory_links (target_type, target_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_links")
    op.execute("DROP TABLE IF EXISTS memory_elements")
    op.execute("DROP TYPE IF EXISTS memory_link_target_type")
    op.execute("DROP TYPE IF EXISTS memory_link_relation")
    op.execute("DROP TYPE IF EXISTS memory_status")
    op.execute("DROP TYPE IF EXISTS memory_tier")
    op.execute("DROP TYPE IF EXISTS memory_kind")
