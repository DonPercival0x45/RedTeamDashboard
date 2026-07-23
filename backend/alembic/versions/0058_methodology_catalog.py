"""v3 A1 — methodology catalog + engagement snapshot + coverage FK

Adds the catalog Track A step A1: a selectable methodology (PTES / MITRE
ATT&CK / minimal OSINT starter) as a tree of coverage nodes; the engagement
gets a frozen JSONB snapshot at selection time so later catalog edits can't
break in-flight tracking (architecture-v2-plan §2a).

Three moves:

1. Two new tables — ``methodologies`` (catalog) and ``methodology_nodes``
   (the tree, expressed as (node_id, parent_node_id) strings). ``node_id`` is
   the same stable string identifier ``coverage_records.node_id`` already
   carries — nodes are NOT FK'd from coverage records, only referenced by
   string.
2. Three new columns on ``engagements``: ``methodology_id`` (nullable FK for
   provenance), ``methodology_snapshot`` (JSONB, the frozen tree), and
   ``methodology_selected_at``.
3. ``coverage_records.methodology_id`` was a plain UUID in 0056 pending A1 —
   this migration upgrades it to a real nullable FK.

The ``coverage_node_tier`` enum was created in 0056 (baseline / exploration)
and is reused here for ``methodology_nodes.tier`` — same vocabulary A2 reads.
"""
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- methodologies catalog -------------------------------------------
    op.execute(
        """
        CREATE TABLE methodologies (
            id UUID PRIMARY KEY,
            slug VARCHAR(80) NOT NULL,
            version INT NOT NULL DEFAULT 1,
            name VARCHAR(200) NOT NULL,
            description TEXT,
            source_url VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_methodologies_slug_version UNIQUE (slug, version)
        )
        """
    )
    op.execute("CREATE INDEX ix_methodologies_slug ON methodologies (slug)")

    # --- methodology_nodes (the tree) ------------------------------------
    # ``tier`` reuses the ``coverage_node_tier`` enum created in 0056.
    op.execute(
        """
        CREATE TABLE methodology_nodes (
            id UUID PRIMARY KEY,
            methodology_id UUID NOT NULL REFERENCES methodologies(id) ON DELETE CASCADE,
            node_id VARCHAR(200) NOT NULL,
            parent_node_id VARCHAR(200),
            title VARCHAR(200) NOT NULL,
            description TEXT,
            tier coverage_node_tier NOT NULL,
            asset_class VARCHAR(80) NOT NULL,
            ttl_days INT,
            sort_order INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_methodology_nodes_ident UNIQUE (methodology_id, node_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_methodology_nodes_methodology "
        "ON methodology_nodes (methodology_id)"
    )
    op.execute(
        "CREATE INDEX ix_methodology_nodes_tier_asset "
        "ON methodology_nodes (methodology_id, tier, asset_class)"
    )

    # --- engagement columns ----------------------------------------------
    # Nullable FK — legacy + pre-A1 engagements have no methodology; a fresh
    # v3 engagement selects one via the wizard. ON DELETE SET NULL so a
    # (hypothetical) catalog deletion doesn't cascade-nuke engagement rows;
    # the snapshot column still preserves the tree state.
    op.execute(
        """
        ALTER TABLE engagements
            ADD COLUMN methodology_id UUID REFERENCES methodologies(id) ON DELETE SET NULL,
            ADD COLUMN methodology_snapshot JSONB,
            ADD COLUMN methodology_selected_at TIMESTAMPTZ
        """
    )
    op.execute(
        "CREATE INDEX ix_engagements_methodology "
        "ON engagements (methodology_id)"
    )

    # --- coverage_records.methodology_id → real FK -----------------------
    # 0056 declared it as a plain nullable UUID pending A1. Refine to a real
    # FK now that the target table exists. Stays nullable so pre-selection
    # coverage attempts (rare, but legal) can still land.
    op.execute(
        """
        ALTER TABLE coverage_records
            ADD CONSTRAINT fk_coverage_records_methodology
            FOREIGN KEY (methodology_id) REFERENCES methodologies(id)
            ON DELETE SET NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE coverage_records DROP CONSTRAINT IF EXISTS "
        "fk_coverage_records_methodology"
    )
    op.execute("DROP INDEX IF EXISTS ix_engagements_methodology")
    op.execute(
        """
        ALTER TABLE engagements
            DROP COLUMN IF EXISTS methodology_selected_at,
            DROP COLUMN IF EXISTS methodology_snapshot,
            DROP COLUMN IF EXISTS methodology_id
        """
    )
    op.execute("DROP TABLE IF EXISTS methodology_nodes")
    op.execute("DROP TABLE IF EXISTS methodologies")
