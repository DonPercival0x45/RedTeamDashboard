"""Auto-reassess on work-item resolve: rate limit + best-effort scheduling.

When a work item resolves the strategist kicks off a reassess in the background,
but rate-limited per engagement so resolving several items in a row fires at
most one LLM run within the cooldown window.

Unit-style (no DB): a fake Redis exercises the SET NX cooldown and the scheduler
is observed by patching threading.Thread.
"""
from __future__ import annotations

import uuid


def test_should_fire_acquires_then_cooldowns() -> None:
    from app.services.engagement_strategist import _auto_reassess_should_fire

    store: dict[str, str] = {}

    class FakeRedis:
        def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
            if nx and key in store:
                return False
            store[key] = value
            return True

    redis_client = FakeRedis()
    engagement_id = uuid.uuid4()

    assert _auto_reassess_should_fire(redis_client, engagement_id) is True
    # Second call within the cooldown window is a no-op (lock held).
    assert _auto_reassess_should_fire(redis_client, engagement_id) is False


def test_maybe_schedule_fires_once_then_cooldowns(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services import engagement_strategist as service

    store: dict[str, str] = {}

    class FakeRedis:
        def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
            if nx and key in store:
                return False
            store[key] = value
            return True

    started: list[tuple] = []

    class FakeThread:
        def __init__(self, target, args, daemon):  # type: ignore[no-untyped-def]
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self) -> None:
            # Record that a background run was scheduled; don't actually run the
            # LLM/thread.
            started.append(self.args)

    monkeypatch.setattr(service.threading, "Thread", FakeThread)

    redis_client = FakeRedis()
    engagement_id = uuid.uuid4()
    acting_user_id = uuid.uuid4()

    service.maybe_schedule_auto_reassess(redis_client, engagement_id, acting_user_id)
    service.maybe_schedule_auto_reassess(redis_client, engagement_id, acting_user_id)
    service.maybe_schedule_auto_reassess(redis_client, engagement_id, acting_user_id)

    # Three resolves, one cooldown window -> exactly one background schedule.
    assert len(started) == 1
    assert started[0][1] == engagement_id
    assert started[0][2] == acting_user_id


def test_maybe_schedule_never_raises_on_redis_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services import engagement_strategist as service

    class BrokenRedis:
        def set(self, *a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("redis down")

    # Must not raise — the resolve that calls this must never fail because of it.
    service.maybe_schedule_auto_reassess(BrokenRedis(), uuid.uuid4(), uuid.uuid4())
