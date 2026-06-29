"""Ephemeral-key resolver + worker lookup wiring.

Covers:
- ``resolve_for_user`` returns the user's most-recently-updated row for a
  provider, raises ``NoProviderKeyError`` when nothing matches.
- The ``RunRunner._resolve_graph`` lookup threads the resolved api_key and
  endpoint into the model mapping handed to ``graph_factory``.
- An envelope missing ``acting_user_id`` is a hard protocol error — the
  runner raises RuntimeError rather than silently falling through to env.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import redis as redis_lib
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import User
from app.services import ephemeral_provider_key as keys
from app.services.ephemeral_provider_key import (
    NoProviderKeyError,
    resolve_for_user,
)
from app.worker.runner import RunRunner


@pytest.fixture()
def redis_client() -> Iterator[redis_lib.Redis]:
    r = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield r
    finally:
        r.close()


@pytest.fixture(autouse=True)
def _flush_keys(redis_client: redis_lib.Redis) -> Iterator[None]:
    for key in redis_client.scan_iter("provider_keys:*"):
        redis_client.delete(key)
    yield
    for key in redis_client.scan_iter("provider_keys:*"):
        redis_client.delete(key)


def _make_user(db: Session) -> User:
    u = User(
        email=f"wireup-{uuid.uuid4().hex[:6]}@example.com",
        display_name="wireup",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _seed(
    redis_client: redis_lib.Redis,
    user: User,
    *,
    provider: str,
    api_key: str | None,
    is_local: bool = False,
    endpoint: str | None = None,
    name: str | None = None,
    kind: str = "model_provider",
) -> dict[str, Any]:
    entry = {
        "id": str(uuid.uuid4()),
        "user_id": str(user.id),
        "kind": kind,
        "name": name or f"{provider}-{uuid.uuid4().hex[:6]}",
        "provider": provider,
        "is_local": is_local,
        "models": [],
        "endpoint": endpoint,
        "api_key": api_key,
        "key_last4": (api_key[-4:] if api_key else None),
        "extra": {},
    }
    return keys.store(redis_client, user_id=user.id, entry=entry)


# ── resolver ─────────────────────────────────────────────────────────────


def test_resolver_returns_user_key(
    db: Session, redis_client: redis_lib.Redis
) -> None:
    user = _make_user(db)
    _seed(redis_client, user, provider="anthropic", api_key="sk-ant-resolved-1234")
    out = resolve_for_user(redis_client, user_id=user.id, provider="anthropic")
    assert out.api_key == "sk-ant-resolved-1234"
    assert out.is_local is False


def test_resolver_returns_endpoint_for_local(
    db: Session, redis_client: redis_lib.Redis
) -> None:
    user = _make_user(db)
    _seed(
        redis_client,
        user,
        provider="ollama",
        api_key=None,
        is_local=True,
        endpoint="http://localhost:11434",
    )
    out = resolve_for_user(redis_client, user_id=user.id, provider="ollama")
    assert out.api_key is None
    assert out.endpoint == "http://localhost:11434"
    assert out.is_local is True


def test_resolver_no_row_raises(
    db: Session, redis_client: redis_lib.Redis
) -> None:
    user = _make_user(db)
    with pytest.raises(NoProviderKeyError):
        resolve_for_user(redis_client, user_id=user.id, provider="anthropic")


def test_resolver_picks_most_recent_when_multiple(
    db: Session, redis_client: redis_lib.Redis
) -> None:
    user = _make_user(db)
    _seed(redis_client, user, provider="anthropic", api_key="sk-ant-old", name="old")
    _seed(redis_client, user, provider="anthropic", api_key="sk-ant-new", name="new")
    out = resolve_for_user(redis_client, user_id=user.id, provider="anthropic")
    # MRU by updated_at — newest wins. Both entries are written by store()
    # which stamps updated_at to now(); the later store wins.
    assert out.api_key == "sk-ant-new"


def test_resolver_ignores_mcp_kind_rows(
    db: Session, redis_client: redis_lib.Redis
) -> None:
    user = _make_user(db)
    _seed(
        redis_client,
        user,
        provider="anthropic",  # name collision is fine; kind differs
        api_key="ghp-xxxx",
        kind="mcp_server",
        endpoint="https://example.test",
    )
    with pytest.raises(NoProviderKeyError):
        resolve_for_user(redis_client, user_id=user.id, provider="anthropic")


# ── runner lookup ────────────────────────────────────────────────────────


class _CapturingFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any] | None] = []

    def __call__(
        self,
        model: Any,
        allowed_tools: Any = None,
        mcp_url: Any = None,
        lease_token: Any = None,
    ) -> object:
        del allowed_tools, mcp_url, lease_token
        self.calls.append(dict(model) if model else None)
        return object()


def test_runner_threads_user_key_into_graph_factory(
    db: Session, redis_client: redis_lib.Redis
) -> None:
    user = _make_user(db)
    _seed(redis_client, user, provider="anthropic", api_key="sk-ant-runner-9999")

    factory = _CapturingFactory()
    runner = RunRunner(
        graph_factory=factory,
        redis_client=redis_client,
        session_factory=SessionLocal,
    )
    runner._resolve_graph(
        {
            "type": "run.start",
            "thread_id": "t1",
            "model": {"provider": "anthropic", "name": "claude-opus-4-7"},
            "acting_user_id": str(user.id),
        }
    )
    assert factory.calls == [
        {
            "provider": "anthropic",
            "name": "claude-opus-4-7",
            "api_key": "sk-ant-runner-9999",
            "endpoint": None,
        }
    ]


def test_runner_envelope_without_acting_user_raises(
    db: Session, redis_client: redis_lib.Redis
) -> None:
    """No acting_user_id on a model-bearing envelope is a protocol error —
    the runner refuses rather than falling back to env-var keys."""
    factory = _CapturingFactory()
    runner = RunRunner(
        graph_factory=factory,
        redis_client=redis_client,
        session_factory=SessionLocal,
    )
    with pytest.raises(RuntimeError, match="acting_user_id"):
        runner._resolve_graph(
            {
                "type": "run.start",
                "thread_id": "t1",
                "model": {"provider": "anthropic", "name": "claude-opus-4-7"},
            }
        )


def test_runner_propagates_no_key_error(
    db: Session, redis_client: redis_lib.Redis
) -> None:
    """Worker handle() catches NoProviderKeyError raised by the runner
    lookup and surfaces a run.errored — verified here by re-raising past
    _resolve_graph."""
    user = _make_user(db)  # no anthropic key cached
    factory = _CapturingFactory()
    runner = RunRunner(
        graph_factory=factory,
        redis_client=redis_client,
        session_factory=SessionLocal,
    )
    with pytest.raises(NoProviderKeyError):
        runner._resolve_graph(
            {
                "type": "run.start",
                "thread_id": "t1",
                "model": {"provider": "anthropic", "name": "claude-opus-4-7"},
                "acting_user_id": str(user.id),
            }
        )
