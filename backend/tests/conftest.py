"""Test fixtures.

DB tests assume the docker-compose Postgres is reachable at the URL in
``settings.database_url`` (or DATABASE_URL env var) and that ``alembic upgrade
head`` has been run. CI will spin up an isolated Postgres separately.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings

# Local compose's backend container points at the operator's live `rtd`
# database. Refuse that target before importing SessionLocal so an accidental
# `docker compose exec backend pytest` cannot commit fixtures into real local
# engagements. CI provisions an isolated database that happens to share the
# name; unusual intentional setups can opt in explicitly.
_database_name = make_url(settings.database_url).database
if (
    _database_name == "rtd"
    and not os.environ.get("CI")
    and os.environ.get("RTD_ALLOW_LIVE_TEST_DB") != "1"
):
    pytest.exit(
        "Refusing to run tests against the local live 'rtd' database. "
        "Use `make test` (isolated rtd_test) or set DATABASE_URL to a "
        "dedicated test database. Set RTD_ALLOW_LIVE_TEST_DB=1 only when "
        "the database is independently disposable.",
        returncode=2,
    )

from app.db.session import SessionLocal  # noqa: E402


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
