"""`rtd tail` — stream SSE events from a run.

Two modes:
- Default (--no-thread): tail mode, like ``tail -f``. Only events from `now`
  onward; nothing from before this command started.
- --thread + optional --since: scoped to one thread, optionally replaying from
  the given Last-Event-ID.
"""
from __future__ import annotations

import json
import signal
from typing import Any

import click

from xray.client import stream_events
from xray.output import console


@click.command()
@click.argument("slug")
@click.option("--thread", "thread_id", help="Filter to one thread_id.")
@click.option("--since", "last_event_id",
              help="Resume from this Last-Event-ID instead of starting at $now.")
@click.pass_context
def tail(
    ctx: click.Context,
    slug: str,
    thread_id: str | None,
    last_event_id: str | None,
) -> None:
    """Stream events for project SLUG until you Ctrl-C."""
    _tail_events(ctx, slug, thread_id=thread_id, last_event_id=last_event_id)


def _tail_events(
    ctx: click.Context,
    slug: str,
    *,
    thread_id: str | None = None,
    last_event_id: str | None = None,
) -> None:
    profile = ctx.obj.config.resolve(ctx.obj.profile_name)
    params: dict[str, str] = {}
    if thread_id:
        params["thread"] = thread_id

    # Make Ctrl-C exit cleanly without a 130 stack trace.
    signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    try:
        with stream_events(
            profile,
            f"/projects/{slug}/events",
            params=params or None,
            last_event_id=last_event_id,
        ) as events:
            for ev in events:
                _print_event(ev, json_mode=ctx.obj.json_mode)
    except KeyboardInterrupt:
        console.print("\n[dim]disconnected.[/dim]")


def _print_event(ev: dict[str, Any], *, json_mode: bool) -> None:
    if json_mode:
        json.dump(ev, click.get_text_stream("stdout"))
        click.echo()
        return
    kind = ev.get("event") or "event"
    data = ev.get("data") or {}
    color = _COLORS.get(kind, "white")
    headline = _headline(kind, data)
    console.print(f"[{color}]{kind:18s}[/{color}] {headline}")


_COLORS = {
    "run.started": "cyan",
    "run.completed": "green",
    "run.errored": "red",
    "approval.pending": "yellow",
    "approval.decided": "blue",
    "finding.created": "magenta",
    "tool.auto_approved": "dim",
}


def _headline(kind: str, data: dict[str, Any]) -> str:
    """One-line summary, kind-specific so the stream is scannable."""
    if kind == "finding.created":
        sev = data.get("severity", "?")
        title = data.get("title") or "(no title)"
        return f"[bold]{sev}[/bold] {title}"
    if kind == "approval.pending":
        return f"{data.get('tool', '?')} {data.get('args', {})}  -> approval_id={data.get('approval_id')}"
    if kind == "approval.decided":
        return f"{data.get('tool', '?')} {data.get('status', '?')}"
    if kind == "tool.auto_approved":
        return f"{data.get('tool', '?')} via grant"
    if kind in ("run.started", "run.completed", "run.errored"):
        return f"thread={data.get('thread_id', '?')[:8]}"
    return ""
