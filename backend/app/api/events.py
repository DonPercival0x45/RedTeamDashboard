"""Server-Sent Events endpoint that re-streams ``runs:{eid}:events`` to clients.

Wire format (standard SSE)::

    id: 1779380819356-0
    event: finding.created
    data: {"type":"finding.created","tool":"subfinder",...}

    : heartbeat

    id: 1779380821001-0
    event: run.completed
    data: {"type":"run.completed",...}

- ``id:`` is the Redis Stream message ID so clients can resume via the
  standard ``Last-Event-ID`` header on reconnect.
- ``event:`` is the payload's ``type`` field, so client JS can use
  ``addEventListener('finding.created', ...)`` per event kind.
- Comments (lines starting with ``:``) are ignored by EventSource clients and
  keep proxies from closing idle connections. We emit one every ~15s.

EventSource (the browser API) can't send custom headers — the frontend uses
``fetch`` + ReadableStream so the ``X-User-Id`` dep still applies.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from sqlalchemy import select
from starlette.responses import StreamingResponse

from app.api.deps import AsyncRedisClient, CurrentUser, DbSession
from app.models import Engagement
from app.runs.streams import outbound_stream

router = APIRouter()


# XREAD block in ms; also the gap between heartbeats when the stream is idle.
_BLOCK_MS = 15_000
# How often to poll the request for client disconnect. Must be << _BLOCK_MS so
# closed connections are detected quickly instead of waiting out the XREAD
# block timeout — that delay matters for tests and for clients that reconnect.
_DISCONNECT_POLL_S = 0.5


def _sse_frame(*, event: str | None, msg_id: str, data: str) -> bytes:
    lines: list[str] = [f"id: {msg_id}"]
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {data}")
    lines.append("")  # terminator (frame ends with blank line)
    lines.append("")
    return "\n".join(lines).encode("utf-8")


async def _wait_disconnect(request: Request) -> None:
    while not await request.is_disconnected():
        await asyncio.sleep(_DISCONNECT_POLL_S)


async def _event_stream(
    redis: Any,
    stream: str,
    start_id: str,
    thread_filter: str | None,
    request: Request,
) -> AsyncIterator[bytes]:
    last_id = start_id
    # Send a primer comment so the client sees something on connect and the
    # browser fires onopen — important for the UI to remove a "connecting…" state.
    yield b": connected\n\n"

    # Long-lived watcher coroutine: completes the moment the client closes the
    # connection. We race each XREAD against it so disconnect is detected
    # within _DISCONNECT_POLL_S rather than waiting out the XREAD block.
    disconnect_task = asyncio.create_task(_wait_disconnect(request))
    try:
        while True:
            if disconnect_task.done():
                return

            read_task: asyncio.Task[Any] = asyncio.create_task(
                redis.xread({stream: last_id}, block=_BLOCK_MS, count=20)
            )
            done, _pending = await asyncio.wait(
                {read_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if disconnect_task in done:
                read_task.cancel()
                return

            try:
                response = read_task.result()
            except Exception as exc:  # noqa: BLE001 — surface to client
                err = json.dumps({"error": str(exc)})
                yield _sse_frame(event="stream.error", msg_id="0-0", data=err)
                return

            if not response:
                yield b": heartbeat\n\n"
                continue

            for _stream_name, messages in response:
                for msg_id, fields in messages:
                    last_id = msg_id
                    raw = fields.get("data")
                    if not isinstance(raw, str):
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if thread_filter and payload.get("thread_id") != thread_filter:
                        continue

                    yield _sse_frame(
                        event=payload.get("type"),
                        msg_id=msg_id,
                        data=raw,
                    )
    finally:
        disconnect_task.cancel()


@router.get("/engagements/{slug}/events")
async def stream_events(
    slug: str,
    request: Request,
    session: DbSession,
    user: CurrentUser,  # noqa: ARG001 — gates the endpoint via dep resolution
    redis: AsyncRedisClient,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    thread: Annotated[
        str | None,
        Query(description="Filter events to a single thread_id."),
    ] = None,
) -> StreamingResponse:
    eng = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail="engagement not found")

    stream = outbound_stream(eng.id)
    start_id = last_event_id if last_event_id else "$"

    return StreamingResponse(
        _event_stream(redis, stream, start_id, thread, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Disable buffering at nginx / other reverse proxies.
            "X-Accel-Buffering": "no",
        },
    )
