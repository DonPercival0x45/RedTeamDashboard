"""Authentication boundary regression tests."""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.main import app
from app.models import User, UserRole


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def impersonation_targets(db: Session) -> Iterator[tuple[User, User]]:
    suffix = uuid.uuid4().hex[:8]
    user = User(email=f"prod-user-{suffix}@example.com", role=UserRole.user)
    admin = User(email=f"prod-admin-{suffix}@example.com", role=UserRole.admin)
    db.add_all([user, admin])
    db.commit()
    db.refresh(user)
    db.refresh(admin)
    try:
        yield user, admin
    finally:
        db.delete(user)
        db.delete(admin)
        db.commit()


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ("local", True),
        ("dev", True),
        ("development", True),
        ("test", True),
        ("testing", True),
        ("prod", False),
        ("production", False),
        ("unexpected", False),
    ],
)
def test_x_user_id_environment_allowlist(env: str, expected: bool) -> None:
    assert Settings(env=env, _env_file=None).allow_x_user_id is expected


def test_prod_rejects_existing_user_and_admin_impersonation(
    client: TestClient,
    impersonation_targets: tuple[User, User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "env", "prod")

    for target in impersonation_targets:
        response = client.get("/me", headers={"X-User-Id": str(target.id)})
        assert response.status_code == 401
        assert "disabled" in response.json()["detail"]


def test_prod_rejects_x_user_id_without_creating_caller_chosen_user(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "env", "prod")
    caller_chosen_id = uuid.uuid4()

    response = client.get("/me", headers={"X-User-Id": str(caller_chosen_id)})

    assert response.status_code == 401
    assert db.execute(
        select(User).where(User.id == caller_chosen_id)
    ).scalar_one_or_none() is None
