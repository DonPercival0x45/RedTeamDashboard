"""Drive an OSINT engagement end-to-end against the live compose stack.

Creates an engagement + scope items, pushes ``run.start`` onto the inbound
stream, prints events from the outbound stream as they arrive, and prompts
you on each ``approval.pending`` (approve / deny / edit args). Cleans up the
engagement on exit unless ``--keep`` is passed.

Run inside the backend container so it can reach Postgres + Redis + the
FastAPI app over localhost::

    docker compose -f infra/docker-compose.yml exec backend \\
        python -m scripts.drive_engagement \\
            --scope acme.com \\
            --prompt "enumerate acme.com"
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

import httpx
import redis as redis_lib
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Engagement, EngagementStatus, ScopeItem, ScopeKind
from app.runs.events import encode_command
from app.runs.streams import inbound_stream, outbound_stream

API_BASE = "http://localhost:8000"
USER_HEADER = "drive-engagement@example.com"
TERMINAL_EVENTS = {"run.completed", "run.errored"}
DEFAULT_DEADLINE_S = 180.0


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------


def _infer_kind(value: str) -> ScopeKind:
    if value.startswith(("http://", "https://")):
        return ScopeKind.url
    if "/" in value:
        return ScopeKind.cidr
    # bare IPv4 or IPv6
    if all(ch.isdigit() or ch == "." for ch in value) and value.count(".") == 3:
        return ScopeKind.ip
    if ":" in value:
        return ScopeKind.ip
    return ScopeKind.domain


def _create_engagement(db: Session, name: str, scopes: list[str]) -> Engagement:
    eng = Engagement(
        name=name,
        slug=f"drive-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    for raw in scopes:
        db.add(
            ScopeItem(
                engagement_id=eng.id,
                kind=_infer_kind(raw),
                value=raw,
                is_exclusion=False,
            )
        )
    db.commit()
    return eng


def _cleanup(
    db: Session, redis_client: redis_lib.Redis, engagement_id: uuid.UUID
) -> None:
    redis_client.delete(
        inbound_stream(engagement_id), outbound_stream(engagement_id)
    )
    db.execute(
        text("DELETE FROM approvals WHERE engagement_id = :id"),
        {"id": engagement_id},
    )
    db.commit()
    db.execute(text("SELECT flush_engagement(:id)"), {"id": engagement_id})
    db.commit()


# ---------------------------------------------------------------------------
# Event printing + approval prompt
# ---------------------------------------------------------------------------


def _print_event(event: dict[str, Any]) -> None:
    et = event["type"]
    if et == "run.started":
        print(f"[run.started]      thread={event['thread_id']}")
    elif et == "finding.created":
        data = event.get("data") or {}
        summary = ", ".join(f"{k}={v}" for k, v in list(data.items())[:3])
        print(f"[finding.created]  {event.get('tool')} -> {summary}")
    elif et == "approval.pending":
        print(
            f"[approval.pending] {event.get('tool')} "
            f"risk={event.get('risk')} args={event.get('args')}"
        )
    elif et == "run.completed":
        print(f"[run.completed]    thread={event['thread_id']}")
    elif et == "run.errored":
        print(f"[run.errored]      {event.get('error')}")
    else:
        print(f"[{et}] {json.dumps(event)}")


def _prompt_decision(event: dict[str, Any]) -> dict[str, Any]:
    print(f"  tool: {event.get('tool')}")
    print(f"  args: {event.get('args')}")
    print(f"  risk: {event.get('risk')}")
    while True:
        choice = input("  approve / deny / edit > ").strip().lower()
        if choice in {"a", "approve", "y", "yes"}:
            return {"approved": True}
        if choice in {"d", "deny", "n", "no"}:
            reason = input("  reason? > ").strip() or "denied by operator"
            return {"approved": False, "reason": reason}
        if choice == "edit":
            current = json.dumps(event.get("args") or {})
            raw = input(f"  new args (JSON) [{current}] > ").strip()
            if not raw:
                raw = current
            try:
                edited = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"  invalid JSON: {exc}")
                continue
            return {"approved": True, "edited_args": edited}
        print("  please answer: approve / deny / edit")


def _decide(http: httpx.Client, approval_id: str, decision: dict[str, Any]) -> None:
    response = http.post(
        f"{API_BASE}/approvals/{approval_id}/decision",
        json=decision,
        headers={"X-User-Id": USER_HEADER},
        timeout=10.0,
    )
    response.raise_for_status()


# ---------------------------------------------------------------------------
# Event loop
# ---------------------------------------------------------------------------


def _stream_events(
    redis_client: redis_lib.Redis,
    engagement_id: uuid.UUID,
    http: httpx.Client,
    *,
    deadline_s: float,
) -> bool:
    import time

    stream = outbound_stream(engagement_id)
    last_id = "0"
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        result = redis_client.xread({stream: last_id}, block=1000)
        if not result:
            continue
        for _stream_name, messages in result:
            for msg_id, fields in messages:
                last_id = msg_id
                event = json.loads(fields["data"])
                _print_event(event)
                if event.get("type") == "approval.pending":
                    decision = _prompt_decision(event)
                    print(f"  -> POST decision {decision}")
                    _decide(http, event["approval_id"], decision)
                    # Restart the deadline so we wait for the post-resume work.
                    deadline = time.time() + deadline_s
                if event.get("type") in TERMINAL_EVENTS:
                    return event["type"] == "run.completed"
    print("[timeout] no terminal event within deadline")
    return False


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scope",
        action="append",
        required=True,
        metavar="VALUE",
        help="Scope value (repeatable). Kind inferred: domain, CIDR, IP, URL.",
    )
    parser.add_argument(
        "--prompt", required=True, help="Initial human prompt for the agent."
    )
    parser.add_argument("--name", default="drive-test", help="Engagement display name.")
    parser.add_argument(
        "--deadline",
        type=float,
        default=DEFAULT_DEADLINE_S,
        help="Per-quiet-period seconds to wait for events (default 180).",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Do not flush the engagement on exit (leave rows + streams behind).",
    )
    args = parser.parse_args()

    db = SessionLocal()
    redis_client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    http = httpx.Client()

    eng = _create_engagement(db, args.name, args.scope)
    print(f"[engagement] id={eng.id} slug={eng.slug}")
    for raw in args.scope:
        print(f"  scope: {_infer_kind(raw).value} {raw}")

    thread_id = str(uuid.uuid4())
    redis_client.xadd(
        inbound_stream(eng.id),
        encode_command(
            {"type": "run.start", "thread_id": thread_id, "prompt": args.prompt}
        ),
    )
    print(f"[run.start]        thread={thread_id} prompt={args.prompt!r}")

    ok = False
    try:
        ok = _stream_events(
            redis_client, eng.id, http, deadline_s=args.deadline
        )
    except KeyboardInterrupt:
        print("\n[interrupted]")
    finally:
        if not args.keep:
            try:
                _cleanup(db, redis_client, eng.id)
                print(f"[cleanup] flushed engagement {eng.id}")
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                print(f"[cleanup] failed: {exc}")
        else:
            print(f"[kept] engagement {eng.id} (use flush_engagement(uuid) to remove)")
        db.close()
        redis_client.close()
        http.close()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
