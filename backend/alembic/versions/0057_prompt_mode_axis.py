"""v3 B4a — prompt-mode model preferences (separate table)

Adds a per-(analyst, engagement, prompt-mode) model preference so a v3
prompt-mode (strategy / analysis / ideation / coverage_review) can pick a
different model on the same engagement — e.g. ideation on a strong model,
analysis on a mini.

This is the substrate for the one-agent refactor (B4). ``architecture-answers``
§C.2 locked "mode as an orthogonal nullable axis." The literal "add a nullable
``mode`` column to ``agent_model_preference``" turns out to be awkward:
``agent_role`` is NOT NULL and part of that table's unique key, so a v3
mode-pref has no natural ``agent_role`` and the constraint collides. A
**separate table** honors the same intent (mode orthogonal; existing v1 rows
untouched; layered resolution) without the ``agent_role`` coupling.

Nothing existing reads this table yet — ``resolve_model_for_mode`` is the new
resolver alongside v1's role-based ``resolve_agent_model`` (untouched).
"""
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE agent_prompt_mode AS ENUM "
        "('strategy', 'analysis', 'ideation', 'coverage_review')"
    )
    op.execute(
        """
        CREATE TABLE agent_mode_model_preference (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            engagement_id UUID NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
            mode agent_prompt_mode NOT NULL,
            model VARCHAR(200) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (user_id, engagement_id, mode)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_agent_mode_pref_user_eng "
        "ON agent_mode_model_preference (user_id, engagement_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_mode_model_preference")
    op.execute("DROP TYPE IF EXISTS agent_prompt_mode")
