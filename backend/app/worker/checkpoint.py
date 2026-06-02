"""Postgres-backed LangGraph checkpointer for the worker process.

In dev/test we use ``MemorySaver`` — state lives in-process and dies with
the worker. That's fine for unit tests but means a worker restart loses
every in-flight run, including paused approval interrupts. The worker
process uses this saver instead so checkpoints survive restarts and the
approvals UI can still resume a paused run after the worker has cycled.

Setup is idempotent — ``PostgresSaver.setup()`` runs ``CREATE TABLE IF
NOT EXISTS`` for its own ``checkpoint_*`` tables in the same database the
app already uses. Those tables live alongside the SQLAlchemy-managed
schema; alembic doesn't manage them and shouldn't be allowed to drop
them.
"""
from __future__ import annotations

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver

from app.core.config import settings
from app.orchestrator.graph import custom_serde


def _postgres_url() -> str:
    # Settings stores the SQLAlchemy URL (``postgresql+psycopg://``).
    # langgraph + psycopg want the raw libpq URL.
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def build_postgres_checkpointer() -> PostgresSaver:
    conn = psycopg.Connection.connect(
        _postgres_url(),
        autocommit=True,
        prepare_threshold=0,
    )
    saver = PostgresSaver(conn, serde=custom_serde())
    saver.setup()
    return saver
