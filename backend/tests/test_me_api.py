"""``/me`` + ``/me/preferences`` — the acting user's profile and their
per-analyst default model (v1.4.11, roadmap #3 / #12).

Uses the dev ``X-User-Id`` header path (``upsert_user``), same as the
engagements API tests. No live auth needed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def _headers(email: str = "me-test@example.com") -> dict[str, str]:
    return {"X-User-Id": email}


def test_me_has_no_default_model_initially() -> None:
    client = TestClient(app)
    me = client.get("/me", headers=_headers("me-initial@example.com")).json()
    assert me["default_llm_provider"] is None
    assert me["default_llm_model"] is None


def test_set_default_model_round_trips() -> None:
    client = TestClient(app)
    email = "me-roundtrip@example.com"
    patched = client.patch(
        "/me/preferences",
        json={"default_llm_provider": "openai", "default_llm_model": "gpt-4o"},
        headers=_headers(email),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["default_llm_provider"] == "openai"
    assert patched.json()["default_llm_model"] == "gpt-4o"

    # A fresh GET reflects the persisted default.
    me = client.get("/me", headers=_headers(email)).json()
    assert me["default_llm_provider"] == "openai"
    assert me["default_llm_model"] == "gpt-4o"


def test_clear_default_model() -> None:
    client = TestClient(app)
    email = "me-clear@example.com"
    client.patch(
        "/me/preferences",
        json={"default_llm_provider": "anthropic", "default_llm_model": "claude"},
        headers=_headers(email),
    )
    cleared = client.patch(
        "/me/preferences",
        json={"default_llm_provider": None, "default_llm_model": None},
        headers=_headers(email),
    ).json()
    assert cleared["default_llm_provider"] is None
    assert cleared["default_llm_model"] is None
