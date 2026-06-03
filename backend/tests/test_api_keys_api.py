"""API key management surface + X-API-Key auth.

Verifies:
- Bootstrap mint via ``app.scripts.mint_api_key`` writes a usable admin key
- POST /api-keys requires admin scope (cli + viewer are rejected with 403)
- Plaintext key is returned ONCE and never re-fetchable
- Revoking is idempotent and prevents further use
- X-API-Key auth on ``current_user`` resolves to the minting user
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.api_keys import _generate_key
from app.api.deps import hash_api_key
from app.db.session import SessionLocal
from app.main import app
from app.models import APIKey, APIKeyScope, User
from app.scripts.mint_api_key import mint as mint_bootstrap


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _seed_user(db: Session, email: str) -> User:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(email=email, display_name=email.split("@", 1)[0])
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _seed_key(
    db: Session,
    *,
    name: str,
    scope: APIKeyScope,
    created_by: User | None = None,
) -> tuple[APIKey, str]:
    """Persist an APIKey directly and return (row, plaintext) so tests can
    skip the mint endpoint when exercising upstream behavior."""
    raw = _generate_key()
    row = APIKey(
        name=name,
        key_hash=hash_api_key(raw),
        scope=scope,
        created_by=created_by.id if created_by else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw


# ---------------------------------------------------------------------------
# Bootstrap mint script
# ---------------------------------------------------------------------------


def test_bootstrap_mint_creates_usable_admin_key(
    client: TestClient, db: Session
) -> None:
    name = f"bootstrap-{__import__('uuid').uuid4().hex[:8]}"
    token = mint_bootstrap(name, APIKeyScope.admin)
    assert token.startswith("rtd_")

    # Looked up via SHA-256 hash, not the raw token.
    row = db.execute(
        select(APIKey).where(APIKey.key_hash == hash_api_key(token))
    ).scalar_one_or_none()
    assert row is not None
    assert row.scope == APIKeyScope.admin
    assert row.created_by is None  # bootstrap key has no minting user

    # The key admits the caller to admin endpoints.
    response = client.get("/api-keys", headers={"X-API-Key": token})
    assert response.status_code == 200, response.text


def test_bootstrap_mint_refuses_duplicate_active_name(db: Session) -> None:
    name = f"dup-{__import__('uuid').uuid4().hex[:8]}"
    mint_bootstrap(name, APIKeyScope.admin)
    with pytest.raises(RuntimeError, match="already exists"):
        mint_bootstrap(name, APIKeyScope.admin)


# ---------------------------------------------------------------------------
# POST /api-keys (admin only)
# ---------------------------------------------------------------------------


def test_mint_with_admin_key_returns_plaintext_once(
    client: TestClient, db: Session
) -> None:
    user = _seed_user(db, "admin@example.com")
    _, admin_token = _seed_key(db, name="adm", scope=APIKeyScope.admin, created_by=user)

    response = client.post(
        "/api-keys",
        json={"name": "ops cli", "scope": "cli"},
        headers={"X-API-Key": admin_token},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "ops cli"
    assert body["scope"] == "cli"
    assert body["key"].startswith("rtd_")
    assert body["created_by"] == str(user.id)

    # Listing the key now does NOT carry the plaintext.
    listing = client.get("/api-keys", headers={"X-API-Key": admin_token}).json()
    matching = [k for k in listing if k["id"] == body["id"]][0]
    assert "key" not in matching


def test_mint_rejected_for_cli_scope(client: TestClient, db: Session) -> None:
    user = _seed_user(db, "operator@example.com")
    _, cli_token = _seed_key(db, name="cli", scope=APIKeyScope.cli, created_by=user)

    response = client.post(
        "/api-keys",
        json={"name": "would-be admin", "scope": "admin"},
        headers={"X-API-Key": cli_token},
    )
    assert response.status_code == 403
    assert "admin" in response.json()["detail"]


def test_mint_rejected_without_any_key(client: TestClient) -> None:
    response = client.post(
        "/api-keys", json={"name": "n", "scope": "viewer"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api-keys/me — clients learn their own scope (no admin required)
# ---------------------------------------------------------------------------


def test_me_returns_calling_key_metadata(client: TestClient, db: Session) -> None:
    user = _seed_user(db, "viewer-me@example.com")
    row, token = _seed_key(
        db, name="viewer-laptop", scope=APIKeyScope.viewer, created_by=user
    )

    response = client.get("/api-keys/me", headers={"X-API-Key": token})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(row.id)
    assert body["name"] == "viewer-laptop"
    assert body["scope"] == "viewer"
    assert "key" not in body  # plaintext never returned


def test_me_rejects_unknown_key(client: TestClient) -> None:
    response = client.get(
        "/api-keys/me", headers={"X-API-Key": "rtd_does-not-exist"}
    )
    assert response.status_code == 401


def test_me_rejects_missing_key(client: TestClient) -> None:
    response = client.get("/api-keys/me")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


def test_revoke_marks_key_then_blocks_use(
    client: TestClient, db: Session
) -> None:
    user = _seed_user(db, "admin2@example.com")
    _, admin_token = _seed_key(db, name="adm2", scope=APIKeyScope.admin, created_by=user)
    victim, victim_token = _seed_key(
        db, name="victim", scope=APIKeyScope.cli, created_by=user
    )

    # Victim works before revoke.
    pre = client.get("/api-keys", headers={"X-API-Key": admin_token})
    assert pre.status_code == 200

    revoke = client.post(
        f"/api-keys/{victim.id}/revoke",
        headers={"X-API-Key": admin_token},
    )
    assert revoke.status_code == 200
    assert revoke.json()["revoked_at"] is not None

    # Idempotent on a second call.
    again = client.post(
        f"/api-keys/{victim.id}/revoke",
        headers={"X-API-Key": admin_token},
    )
    assert again.status_code == 200

    # Victim can no longer authenticate.
    blocked = client.get("/api-keys", headers={"X-API-Key": victim_token})
    assert blocked.status_code == 401
    assert "revoked" in blocked.json()["detail"]


def test_revoke_unknown_id_returns_404(client: TestClient, db: Session) -> None:
    user = _seed_user(db, "admin3@example.com")
    _, admin_token = _seed_key(db, name="adm3", scope=APIKeyScope.admin, created_by=user)

    response = client.post(
        "/api-keys/00000000-0000-0000-0000-000000000000/revoke",
        headers={"X-API-Key": admin_token},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# X-API-Key auth on existing endpoints (back-compat with X-User-Id preserved)
# ---------------------------------------------------------------------------


def test_x_api_key_resolves_to_minting_user_on_current_user(
    client: TestClient, db: Session
) -> None:
    """A request bearing X-API-Key on an endpoint that uses ``CurrentUser``
    must see the key's creator as the acting user — so audit logs attribute
    actions correctly without anyone setting X-User-Id."""
    user = _seed_user(db, "minter@example.com")
    _, token = _seed_key(db, name="cli-2", scope=APIKeyScope.cli, created_by=user)

    response = client.post(
        "/engagements",
        json={"name": "Created via API key"},
        headers={"X-API-Key": token},
    )
    assert response.status_code == 201, response.text
    assert response.json()["created_by"] == str(user.id)


def test_unknown_api_key_returns_401(client: TestClient) -> None:
    response = client.get(
        "/api-keys", headers={"X-API-Key": "rtd_nonexistent-totally-fake-token"}
    )
    assert response.status_code == 401
    assert "invalid" in response.json()["detail"].lower()
