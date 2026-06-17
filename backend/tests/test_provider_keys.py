"""BYO model + MCP credentials: secret_box round-trip + HTTP surface.

Covers:
- Fernet round-trip + masking helper
- Single create + list returns the masked row (no plaintext leaves the API)
- Bulk import: happy path, duplicates, validation errors, mixed batch
- Patch rotates the key (key_last4 changes, old plaintext is unrecoverable)
- Delete removes the row
- Validation: non-local entries require api_key; mcp_server requires endpoint
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import UserProviderKey
from app.services.secret_box import decrypt, encrypt, last4, mask, reset_for_tests


@pytest.fixture(autouse=True)
def _reset_secret_box_cache() -> Iterator[None]:
    """Tests share one settings.provider_key_master; clear the cached Fernet so
    a misconfigured test cannot cross-contaminate."""
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _hdr(email: str) -> dict[str, str]:
    return {"X-User-Id": email}


# ── secret_box helpers ────────────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip() -> None:
    raw = "sk-ant-very-secret-1234"
    ct = encrypt(raw)
    assert ct != raw
    assert decrypt(ct) == raw


def test_last4_and_mask_helpers() -> None:
    assert last4("sk-ant-abc1234") == "1234"
    assert last4("xyz") == ""  # too short
    assert mask("sk-ant-abc1234").endswith("1234")
    assert mask("").startswith("•")


# ── single create + list ──────────────────────────────────────────────────


def test_create_and_list_returns_masked_row(
    client: TestClient, db: Session
) -> None:
    email = f"byo-{uuid.uuid4().hex[:6]}@example.com"
    res = client.post(
        "/me/provider-keys",
        json={
            "name": "Personal Anthropic",
            "provider": "anthropic",
            "kind": "model_provider",
            "models": ["claude-opus-4-7"],
            "api_key": "sk-ant-secret-abcd",
        },
        headers=_hdr(email),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["key_last4"] == "abcd"
    assert "api_key" not in body
    assert "encrypted_key" not in body

    listed = client.get("/me/provider-keys", headers=_hdr(email)).json()
    assert len(listed) == 1
    assert listed[0]["name"] == "Personal Anthropic"


def test_duplicate_name_returns_409(client: TestClient) -> None:
    email = f"byo-{uuid.uuid4().hex[:6]}@example.com"
    body = {
        "name": "MyKey",
        "provider": "anthropic",
        "kind": "model_provider",
        "api_key": "sk-ant-xxxx",
    }
    res = client.post("/me/provider-keys", json=body, headers=_hdr(email))
    assert res.status_code == 201
    res2 = client.post("/me/provider-keys", json=body, headers=_hdr(email))
    assert res2.status_code == 409


# ── validation ────────────────────────────────────────────────────────────


def test_non_local_requires_api_key(client: TestClient) -> None:
    email = f"byo-{uuid.uuid4().hex[:6]}@example.com"
    res = client.post(
        "/me/provider-keys",
        json={
            "name": "Bad",
            "provider": "anthropic",
            "kind": "model_provider",
        },
        headers=_hdr(email),
    )
    assert res.status_code == 422
    assert "api_key required" in res.text


def test_local_provider_no_key_ok(client: TestClient, db: Session) -> None:
    email = f"byo-{uuid.uuid4().hex[:6]}@example.com"
    res = client.post(
        "/me/provider-keys",
        json={
            "name": "Local Ollama",
            "provider": "ollama",
            "kind": "model_provider",
            "is_local": True,
            "endpoint": "http://localhost:11434",
            "models": ["llama3.1:8b"],
        },
        headers=_hdr(email),
    )
    assert res.status_code == 201, res.text
    assert res.json()["key_last4"] is None
    assert res.json()["is_local"] is True


def test_mcp_server_requires_endpoint(client: TestClient) -> None:
    email = f"byo-{uuid.uuid4().hex[:6]}@example.com"
    res = client.post(
        "/me/provider-keys",
        json={
            "name": "GitHub MCP",
            "provider": "github",
            "kind": "mcp_server",
            "api_key": "ghp_xxxx",
        },
        headers=_hdr(email),
    )
    assert res.status_code == 422
    assert "endpoint" in res.text.lower()


# ── bulk import ───────────────────────────────────────────────────────────


def test_bulk_import_mixed_batch(client: TestClient, db: Session) -> None:
    email = f"byo-{uuid.uuid4().hex[:6]}@example.com"

    # Seed one existing — should land as a duplicate, not break the rest.
    client.post(
        "/me/provider-keys",
        json={
            "name": "Existing",
            "provider": "anthropic",
            "kind": "model_provider",
            "api_key": "sk-ant-existing",
        },
        headers=_hdr(email),
    )

    payload = {
        "providers": [
            {
                "name": "Personal Anthropic",
                "provider": "anthropic",
                "kind": "model_provider",
                "models": ["claude-opus-4-7", "claude-sonnet-4-6"],
                "api_key": "sk-ant-personal-9999",
            },
            {
                "name": "Existing",  # duplicate
                "provider": "anthropic",
                "kind": "model_provider",
                "api_key": "sk-ant-existing-rotated",
            },
            {
                "name": "Local Ollama",
                "provider": "ollama",
                "kind": "model_provider",
                "is_local": True,
                "endpoint": "http://localhost:11434",
                "models": ["llama3.1:8b"],
            },
        ]
    }
    res = client.post(
        "/me/provider-keys/import", json=payload, headers=_hdr(email)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["created"]) == 2
    assert len(body["duplicates"]) == 1
    assert body["duplicates"][0]["name"] == "Existing"
    assert body["errors"] == []

    # Confirm total state in DB
    listed = client.get("/me/provider-keys", headers=_hdr(email)).json()
    assert len(listed) == 3
    names = {r["name"] for r in listed}
    assert {"Existing", "Personal Anthropic", "Local Ollama"} == names


def test_bulk_import_validation_error_row(client: TestClient) -> None:
    email = f"byo-{uuid.uuid4().hex[:6]}@example.com"
    payload = {
        "providers": [
            {
                "name": "Bad",
                "provider": "anthropic",
                "kind": "model_provider",
                # missing api_key
            },
        ]
    }
    res = client.post(
        "/me/provider-keys/import", json=payload, headers=_hdr(email)
    )
    # Pydantic v2 surfaces this as 422 at the request boundary.
    assert res.status_code == 422


# ── rotate + delete ───────────────────────────────────────────────────────


def test_rotate_changes_ciphertext_and_last4(
    client: TestClient, db: Session
) -> None:
    email = f"byo-{uuid.uuid4().hex[:6]}@example.com"
    created = client.post(
        "/me/provider-keys",
        json={
            "name": "Rotate Me",
            "provider": "openai",
            "kind": "model_provider",
            "api_key": "sk-original-1234",
        },
        headers=_hdr(email),
    ).json()
    kid = created["id"]

    row_before = db.get(UserProviderKey, uuid.UUID(kid))
    original_ct = row_before.encrypted_key

    res = client.patch(
        f"/me/provider-keys/{kid}",
        json={"api_key": "sk-rotated-9876"},
        headers=_hdr(email),
    )
    assert res.status_code == 200, res.text
    assert res.json()["key_last4"] == "9876"

    db.expire_all()
    row_after = db.get(UserProviderKey, uuid.UUID(kid))
    assert row_after.encrypted_key != original_ct


def test_delete_removes_row(client: TestClient, db: Session) -> None:
    email = f"byo-{uuid.uuid4().hex[:6]}@example.com"
    created = client.post(
        "/me/provider-keys",
        json={
            "name": "Bye",
            "provider": "openai",
            "kind": "model_provider",
            "api_key": "sk-bye-1234",
        },
        headers=_hdr(email),
    ).json()
    kid = created["id"]

    res = client.delete(f"/me/provider-keys/{kid}", headers=_hdr(email))
    assert res.status_code == 204

    db.expire_all()
    assert db.get(UserProviderKey, uuid.UUID(kid)) is None


# ── isolation across users ────────────────────────────────────────────────


def test_users_cannot_see_each_others_keys(client: TestClient) -> None:
    a = f"a-{uuid.uuid4().hex[:6]}@example.com"
    b = f"b-{uuid.uuid4().hex[:6]}@example.com"
    client.post(
        "/me/provider-keys",
        json={
            "name": "A-only",
            "provider": "anthropic",
            "kind": "model_provider",
            "api_key": "sk-a-xxxx",
        },
        headers=_hdr(a),
    )
    listed = client.get("/me/provider-keys", headers=_hdr(b)).json()
    assert listed == []
