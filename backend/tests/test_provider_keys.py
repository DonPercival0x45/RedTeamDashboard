"""Ephemeral BYO key HTTP surface.

Covers:
- Single create + list returns the masked row (no plaintext leaves the API)
- Bulk import: happy path, duplicates, validation errors, mixed batch
- Patch rotates the key (key_last4 changes)
- Delete removes the row
- DELETE /me/provider-keys (all) wipes the cache (sign-out flow)
- Validation: non-local entries require api_key; mcp_server requires endpoint
- Users can't see each others' keys

Keys live ONLY in Redis (no DB persistence). Each test seeds + reads via
the HTTP surface; cleanup is the per-user ``DELETE /me/provider-keys``.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _flush_provider_keys() -> Iterator[None]:
    """Flush any leftover ``provider_keys:*`` hashes between tests so a
    leaked row from an earlier run can't make an assertion pass spuriously."""
    r = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        for key in r.scan_iter("provider_keys:*"):
            r.delete(key)
        yield
        for key in r.scan_iter("provider_keys:*"):
            r.delete(key)
    finally:
        r.close()


def _hdr(email: str) -> dict[str, str]:
    return {"X-User-Id": email}


# ── single create + list ──────────────────────────────────────────────────


def test_create_and_list_returns_masked_row(client: TestClient) -> None:
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
    assert client.post("/me/provider-keys", json=body, headers=_hdr(email)).status_code == 201
    assert client.post("/me/provider-keys", json=body, headers=_hdr(email)).status_code == 409


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


def test_local_provider_no_key_ok(client: TestClient) -> None:
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


def test_bulk_import_mixed_batch(client: TestClient) -> None:
    email = f"byo-{uuid.uuid4().hex[:6]}@example.com"

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
    res = client.post("/me/provider-keys/import", json=payload, headers=_hdr(email))
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["created"]) == 2
    assert len(body["duplicates"]) == 1
    assert body["duplicates"][0]["name"] == "Existing"
    assert body["errors"] == []

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
            },
        ]
    }
    res = client.post("/me/provider-keys/import", json=payload, headers=_hdr(email))
    assert res.status_code == 422


# ── rotate + delete ───────────────────────────────────────────────────────


def test_rotate_changes_last4(client: TestClient) -> None:
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
    assert created["key_last4"] == "1234"

    res = client.patch(
        f"/me/provider-keys/{created['id']}",
        json={"api_key": "sk-rotated-9876"},
        headers=_hdr(email),
    )
    assert res.status_code == 200, res.text
    assert res.json()["key_last4"] == "9876"


def test_delete_removes_row(client: TestClient) -> None:
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

    res = client.delete(f"/me/provider-keys/{created['id']}", headers=_hdr(email))
    assert res.status_code == 204

    listed = client.get("/me/provider-keys", headers=_hdr(email)).json()
    assert listed == []


def test_delete_all_flushes_cache(client: TestClient) -> None:
    """DELETE /me/provider-keys (no id) is the sign-out flush."""
    email = f"byo-{uuid.uuid4().hex[:6]}@example.com"
    for n in ("A", "B", "C"):
        client.post(
            "/me/provider-keys",
            json={
                "name": n,
                "provider": "anthropic",
                "kind": "model_provider",
                "api_key": f"sk-ant-{n}",
            },
            headers=_hdr(email),
        )
    assert len(client.get("/me/provider-keys", headers=_hdr(email)).json()) == 3
    res = client.delete("/me/provider-keys", headers=_hdr(email))
    assert res.status_code == 204
    assert client.get("/me/provider-keys", headers=_hdr(email)).json() == []


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
    assert client.get("/me/provider-keys", headers=_hdr(b)).json() == []
