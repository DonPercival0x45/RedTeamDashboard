"""Admin user-management surface (roadmap #6).

Covers:
  - GET /admin/users is admin-only
  - PATCH /admin/users/{id}/active: deactivate / reactivate
  - self-lockout guard (can't deactivate yourself)
  - **the is_active enforcement**: a deactivated user's next request 403s
  - audit row user.active_changed is written
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models import AuditLog, User, UserRole

ADMIN_EMAIL = "admin-surface-test@example.com"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def admin_and_targets(
    db: Session,
) -> Iterator[tuple[User, list[User]]]:
    """Seed an admin + hand back the admin and a cleanup list.

    Tests append any target users they create to the returned list so
    teardown can delete them (and the admin) without touching real rows.
    """
    admin = db.execute(select(User).where(User.email == ADMIN_EMAIL)).scalar_one_or_none()
    if admin is None:
        admin = User(
            email=ADMIN_EMAIL,
            display_name="Admin Surface Test",
            role=UserRole.admin,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
    elif admin.role != UserRole.admin or not admin.is_active:
        admin.role = UserRole.admin
        admin.is_active = True
        db.commit()
        db.refresh(admin)

    targets: list[User] = []
    try:
        yield admin, targets
    finally:
        for u in targets:
            # remove audit rows we wrote for this user, then the user
            db.execute(
                AuditLog.__table__.delete().where(
                    AuditLog.actor_id == str(u.id)
                )
            )
            db.delete(u)
        # admin's own audit rows
        db.execute(
            AuditLog.__table__.delete().where(AuditLog.actor_id == str(admin.id))
        )
        db.commit()


def _make_target(db: Session, email: str) -> User:
    u = User(
        email=email,
        display_name=email.split("@", 1)[0],
        role=UserRole.user,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _admin_headers(admin: User) -> dict[str, str]:
    return {"X-User-Id": ADMIN_EMAIL}


def test_list_users_requires_admin(
    client: TestClient, db: Session, admin_and_targets: tuple[User, list[User]]
) -> None:
    admin, targets = admin_and_targets
    target = _make_target(db, f"target-list-{uuid.uuid4().hex[:8]}@example.com")
    targets.append(target)
    # Non-admin (the target) is forbidden.
    r = client.get("/admin/users", headers={"X-User-Id": target.email})
    assert r.status_code == 403
    # Admin can list.
    r = client.get("/admin/users", headers=_admin_headers(admin))
    assert r.status_code == 200
    emails = {row["email"] for row in r.json()}
    assert ADMIN_EMAIL in emails
    assert target.email in emails


def test_deactivate_blocks_sign_in(
    client: TestClient, db: Session, admin_and_targets: tuple[User, list[User]]
) -> None:
    """The whole point of #6: deactivating must actually revoke access."""
    admin, targets = admin_and_targets
    target = _make_target(db, f"target-block-{uuid.uuid4().hex[:8]}@example.com")
    targets.append(target)

    # Target can hit the API while active.
    r = client.get("/engagements", headers={"X-User-Id": target.email})
    assert r.status_code == 200

    # Admin deactivates them.
    r = client.patch(
        f"/admin/users/{target.id}/active",
        json={"is_active": False},
        headers=_admin_headers(admin),
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # Their very next request is now rejected via the is_active gate.
    r = client.get("/engagements", headers={"X-User-Id": target.email})
    assert r.status_code == 403


def test_reactivate_restores_access(
    client: TestClient, db: Session, admin_and_targets: tuple[User, list[User]]
) -> None:
    admin, targets = admin_and_targets
    target = _make_target(db, f"target-react-{uuid.uuid4().hex[:8]}@example.com")
    targets.append(target)

    client.patch(
        f"/admin/users/{target.id}/active",
        json={"is_active": False},
        headers=_admin_headers(admin),
    )
    assert (
        client.get("/engagements", headers={"X-User-Id": target.email}).status_code
        == 403
    )

    # Reactivate -> access restored.
    r = client.patch(
        f"/admin/users/{target.id}/active",
        json={"is_active": True},
        headers=_admin_headers(admin),
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is True
    assert (
        client.get("/engagements", headers={"X-User-Id": target.email}).status_code
        == 200
    )


def test_cannot_deactivate_self(
    client: TestClient, db: Session, admin_and_targets: tuple[User, list[User]]
) -> None:
    admin, _ = admin_and_targets
    r = client.patch(
        f"/admin/users/{admin.id}/active",
        json={"is_active": False},
        headers=_admin_headers(admin),
    )
    assert r.status_code == 400
    assert "yourself" in r.json()["detail"]


def test_active_change_is_audited(
    client: TestClient, db: Session, admin_and_targets: tuple[User, list[User]]
) -> None:
    admin, targets = admin_and_targets
    target = _make_target(db, f"target-audit-{uuid.uuid4().hex[:8]}@example.com")
    targets.append(target)

    client.patch(
        f"/admin/users/{target.id}/active",
        json={"is_active": False},
        headers=_admin_headers(admin),
    )

    row = db.execute(
        select(AuditLog)
        .where(AuditLog.event_type == "user.active_changed")
        .order_by(AuditLog.created_at.desc())
    ).scalars().first()
    assert row is not None
    payload = row.payload
    assert payload["target_user_id"] == str(target.id)
    assert payload["from_active"] is True
    assert payload["to_active"] is False


def test_idempotent_active_change(
    client: TestClient, db: Session, admin_and_targets: tuple[User, list[User]]
) -> None:
    admin, targets = admin_and_targets
    target = _make_target(db, f"target-idem-{uuid.uuid4().hex[:8]}@example.com")
    targets.append(target)
    # Target starts active; setting active=true again is a 200 no-op.
    r = client.patch(
        f"/admin/users/{target.id}/active",
        json={"is_active": True},
        headers=_admin_headers(admin),
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is True
