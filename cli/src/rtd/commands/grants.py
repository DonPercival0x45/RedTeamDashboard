"""`rtd grants ...` — list / revoke per-(engagement, tool) session grants."""
from __future__ import annotations

import click
from rich.table import Table

from rtd.output import console, emit


@click.group(name="grants")
def grants_group() -> None:
    """Manage session authorizations (the "remember for session" approvals)."""


@grants_group.command("list")
@click.argument("slug")
@click.option("--all", "include_inactive", is_flag=True,
              help="Include revoked grants too (default is active only).")
@click.pass_context
def list_grants(ctx: click.Context, slug: str, include_inactive: bool) -> None:
    """List session grants on engagement SLUG."""
    params: dict[str, str] = {}
    if not include_inactive:
        params["active"] = "true"
    with ctx.obj.client() as c:
        eng = c.get(f"/engagements/{slug}")
        rows = c.get(f"/engagements/{eng['id']}/authorizations", params=params or None)
    t = Table(title=f"Grants ({slug})")
    t.add_column("id", style="bold")
    t.add_column("tool")
    t.add_column("granted")
    t.add_column("revoked")
    for r in rows:
        t.add_row(r["id"], r["tool_name"], r["created_at"][:19],
                  (r.get("revoked_at") or "")[:19])
    emit(rows, json_mode=ctx.obj.json_mode, table=t)


@grants_group.command("revoke")
@click.argument("authorization_id")
@click.pass_context
def revoke(ctx: click.Context, authorization_id: str) -> None:
    """Revoke session grant AUTHORIZATION_ID."""
    with ctx.obj.client() as c:
        c.post(f"/authorizations/{authorization_id}/revoke")
    console.print(f"revoked grant [bold]{authorization_id}[/bold]")
