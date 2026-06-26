"""SSE endpoint that re-streams ``runs:{eid}:events`` to HTTP clients.

Each test seeds raw events on the Project's outbound stream, opens an SSE
connection using TestClient's streaming mode, and verifies the SSE framing,
the ``Last-Event-ID`` resume contract, and the optional ``?thread=`` filter.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.main import app
from app.models import Project, ProjectStatus
from app.runs.events import encode_event
from app.runs.streams import inbound_stream, outbound_stream

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def redis_client() -> Iterator[redis_lib.Redis]:
    r = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield r
    finally:
        r.close()


_ProjectModel = Project  # save class reference before fixture shadows the name


@pytest.fixture()
def Project(
    db: Session, redis_client: redis_lib.Redis
) -> Iterator[Project]:
    eng = _ProjectModel(
        name="sse-test",
        slug=f"sse-test-{uuid.uuid4().hex[:8]}",
        status=ProjectStatus.active,
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    try:
        yield eng
    finally:
        redis_client.delete(inbound_stream(eng.id), outbound_stream(eng.id))
        db.execute(
            text("DELETE FROM approvals WHERE project_id = :id"),
            {"id": eng.id},
        )
        db.commit()
        db.execute(text("SELECT flush_engagement(:id)"), {"id": eng.id})
        db.commit()


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {"X-User-Id": "sse-test@example.com"}
    if extra:
        base.update(extra)
    return base


def _push(
    redis_client: redis_lib.Redis,
    project_id: uuid.UUID,
    payload: dict[str, Any],
) -> str:
    """xadd a properly-encoded event, return the resulting stream ID."""
    return redis_client.xadd(outbound_stream(project_id), encode_event(payload))


# ---------------------------------------------------------------------------
# SSE frame parser
# ---------------------------------------------------------------------------


def _parse_sse_frames(buf: str) -> tuple[list[dict[str, Any]], str]:
    """Split a buffer into complete frames + leftover.

    Each frame is the lines between two blank lines; the leftover is whatever
    came after the last complete frame and isn't yet a full frame.
    """
    frames: list[dict[str, Any]] = []
    while "\n\n" in buf:
        block, buf = buf.split("\n\n", 1)
        if not block.strip():
            continue
        if all(line.startswith(":") for line in block.splitlines() if line):
            # Comment-only frame (e.g. heartbeat) — skip
            continue
        frame: dict[str, Any] = {"id": None, "event": None, "data": None}
        for line in block.splitlines():
            if line.startswith(":") or ":" not in line:
                continue
            field, _, value = line.partition(": ")
            if field == "id":
                frame["id"] = value
            elif field == "event":
                frame["event"] = value
            elif field == "data":
                try:
                    frame["data"] = json.loads(value)
                except json.JSONDecodeError:
                    frame["data"] = value
        if frame["data"] is not None:
            frames.append(frame)
    return frames, buf


async def _acollect_frames(
    response: httpx.Response,
    *,
    until_count: int,
    deadline_iters: int = 200,
) -> list[dict[str, Any]]:
    """Async variant: read SSE frames from a streaming httpx response."""
    frames: list[dict[str, Any]] = []
    buf = ""
    async for chunk in response.aiter_text():
        if not chunk:
            continue
        buf += chunk
        new, buf = _parse_sse_frames(buf)
        frames.extend(new)
        if len(frames) >= until_count:
            return frames[:until_count]
        deadline_iters -= 1
        if deadline_iters <= 0:
            break
    return frames


def _async_client() -> httpx.AsyncClient:
    # Streaming tests must talk to the real uvicorn process inside the backend
    # container — httpx's ASGITransport buffers StreamingResponse chunks until
    # the response completes, which never happens for a long-poll SSE stream.
    # Tests run inside the backend container (via `docker compose exec backend
    # pytest`), so localhost:8000 reaches the same FastAPI app under uvicorn.
    return httpx.AsyncClient(base_url="http://localhost:8000", timeout=10.0)


# ---------------------------------------------------------------------------
# Auth + 404
# ---------------------------------------------------------------------------


def test_requires_x_user_id_header(
    client: TestClient, Project: Project
) -> None:
    response = client.get(f"/projects/{Project.slug}/events")
    assert response.status_code == 401


def test_unknown_engagement_returns_404(client: TestClient) -> None:
    response = client.get(
        f"/projects/does-not-exist-{uuid.uuid4().hex[:6]}/events",
        headers=_headers(),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Last-Event-ID replay
# ---------------------------------------------------------------------------


async def test_replays_events_from_last_event_id(
    redis_client: redis_lib.Redis,
    Project: Project,
) -> None:
    thread_id = str(uuid.uuid4())
    id_a = _push(
        redis_client,
        Project.id,
        {"type": "run.started", "thread_id": thread_id, "prompt": "p"},
    )
    id_b = _push(
        redis_client,
        Project.id,
        {
            "type": "finding.created",
            "thread_id": thread_id,
            "tool": "subfinder",
            "args": {"domain": "acme.com"},
            "data": {"subdomains": ["www.acme.com"]},
        },
    )
    _push(
        redis_client,
        Project.id,
        {"type": "run.completed", "thread_id": thread_id},
    )

    # Resume from id_a -> we should get id_b and the run.completed (2 events).
    async with _async_client() as ac, ac.stream(
        "GET",
        f"/projects/{Project.slug}/events",
        headers=_headers({"Last-Event-ID": id_a}),
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = await _acollect_frames(response, until_count=2)

    assert len(frames) >= 2
    assert frames[0]["event"] == "finding.created"
    assert frames[0]["id"] == id_b
    assert frames[0]["data"]["tool"] == "subfinder"
    assert frames[1]["event"] == "run.completed"


# ---------------------------------------------------------------------------
# Thread filter
# ---------------------------------------------------------------------------


async def test_thread_filter_drops_other_threads(
    redis_client: redis_lib.Redis,
    Project: Project,
) -> None:
    target = str(uuid.uuid4())
    other = str(uuid.uuid4())

    _push(redis_client, Project.id, {"type": "run.started", "thread_id": other})
    _push(
        redis_client,
        Project.id,
        {"type": "run.started", "thread_id": target, "prompt": "p"},
    )
    _push(
        redis_client,
        Project.id,
        {"type": "finding.created", "thread_id": other, "tool": "subfinder"},
    )
    _push(
        redis_client,
        Project.id,
        {
            "type": "finding.created",
            "thread_id": target,
            "tool": "crt_sh",
            "data": {"certs": []},
        },
    )
    _push(
        redis_client,
        Project.id,
        {"type": "run.completed", "thread_id": target},
    )

    async with _async_client() as ac, ac.stream(
        "GET",
        f"/projects/{Project.slug}/events",
        params={"thread": target},
        headers=_headers({"Last-Event-ID": "0"}),
    ) as response:
        assert response.status_code == 200
        frames = await _acollect_frames(response, until_count=3)

    assert len(frames) >= 3
    assert all(f["data"]["thread_id"] == target for f in frames)
    types = [f["event"] for f in frames]
    assert types == ["run.started", "finding.created", "run.completed"]


# ---------------------------------------------------------------------------
# Default tail-only behavior (no Last-Event-ID = start at $)
# ---------------------------------------------------------------------------


async def test_tail_default_skips_pre_existing_events(
    redis_client: redis_lib.Redis,
    Project: Project,
) -> None:
    """No Last-Event-ID means start at $ — old events stay invisible."""
    thread_id = str(uuid.uuid4())
    _push(
        redis_client,
        Project.id,
        {"type": "run.started", "thread_id": thread_id, "prompt": "early"},
    )

    async with _async_client() as ac, ac.stream(
        "GET",
        f"/projects/{Project.slug}/events",
        headers=_headers(),
    ) as response:
        assert response.status_code == 200
        buf = ""
        async for chunk in response.aiter_text():
            buf += chunk
            frames, _ = _parse_sse_frames(buf)
            if frames:
                pytest.fail(
                    f"expected no data frames in tail mode, got: {frames}"
                )
            if ": connected" in buf:
                break
