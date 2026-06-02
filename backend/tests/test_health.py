"""/health probes Postgres + Redis, returns 503 when either is unreachable."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import async_redis_client, db_session
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_health_happy_path(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "env": "local", "db": True, "redis": True}


def test_health_reports_degraded_when_db_fails(client: TestClient) -> None:
    class _FakeSession:
        def execute(self, _stmt: Any) -> Any:
            raise RuntimeError("db down")

        def close(self) -> None:
            pass

    def _override() -> Any:
        yield _FakeSession()

    app.dependency_overrides[db_session] = _override
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.pop(db_session, None)

    assert response.status_code == 503
    body = response.json()
    assert body["db"] is False
    assert body["redis"] is True
    assert body["status"] == "degraded"


def test_health_reports_degraded_when_redis_fails(client: TestClient) -> None:
    class _FakeRedis:
        async def ping(self) -> None:
            raise RuntimeError("redis down")

        async def aclose(self) -> None:
            pass

    async def _override() -> AsyncIterator[_FakeRedis]:
        yield _FakeRedis()

    app.dependency_overrides[async_redis_client] = _override
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.pop(async_redis_client, None)

    assert response.status_code == 503
    body = response.json()
    assert body["db"] is True
    assert body["redis"] is False
