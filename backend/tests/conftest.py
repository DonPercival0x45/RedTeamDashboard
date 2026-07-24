"""Test fixtures.

DB tests assume the docker-compose Postgres is reachable at the URL in
``settings.database_url`` (or DATABASE_URL env var) and that ``alembic upgrade
head`` has been run. CI will spin up an isolated Postgres separately.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.db.session import SessionLocal


@pytest.fixture()
def stub_tactical_provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Tactical unit tests independent of Redis-backed BYO credentials.

    Tests that exercise provider fallback itself can override this fixture's
    patch later in their own setup.
    """

    def resolve(_redis: object, **kwargs: object) -> tuple[str, str, object]:
        return (
            str(kwargs["preferred_provider"]),
            str(kwargs["preferred_model"]),
            SimpleNamespace(
                row_id=uuid.UUID(int=0), api_key="test-key", endpoint=None
            ),
        )

    monkeypatch.setattr(
        "app.agents.tactical.resolve_for_user_with_fallback", resolve
    )


@pytest.fixture()
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
