"""Test fixtures.

DB tests assume the docker-compose Postgres is reachable at the URL in
``settings.database_url`` (or DATABASE_URL env var) and that ``alembic upgrade
head`` has been run. CI will spin up an isolated Postgres separately.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.db.session import SessionLocal


@pytest.fixture()
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
