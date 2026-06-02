"""`rtd engagement ...` — CRUD on engagements + scope items.

Mirrors the HTTP surface in ``backend/app/api/engagements.py``. One sub-group
per concept: engagements themselves + nested scope items.
"""
from __future__ import annotations

import click
from rich.table import Table

from rtd.output import emit, kv_table


@click.group(name="engagement")
def engagement_group() -> None:
    """List, create, view, and manage scope items for engagements."""


@engagement_group.command("list")
@click.option("--status", type=click.Choice(["active", "archived", "flushed"]),
              help="Filter by engagement status.")
@click.pass_context
def list_engagements(ctx: click.Context, status: str | None) -> None:
    """List engagements visible to this profile's API key."""
    params = {"status": status} if status else None
    with ctx.obj.client() as c:
        rows = c.get("/engagements", params=params)
    t = Table(title="Engagements")
    t.add_column("slug", style="bold")
    t.add_column("name")
    t.add_column("status")
    t.add_column("created")
    for r in rows:
        t.add_row(r["slug"], r["name"], r["status"], r["created_at"][:19])
    emit(rows, json_mode=ctx.obj.json_mode, table=t)


@engagement_group.command("create")
@click.option("--name", required=True, help="Human-readable engagement name.")
@click.option("--slug", help="Override the auto-generated slug.")
@click.pass_context
def create(ctx: click.Context, name: str, slug: str | None) -> None:
    """Create a new active engagement."""
    body: dict[str, str] = {"name": name}
    if slug:
        body["slug"] = slug
    with ctx.obj.client() as c:
        eng = c.post("/engagements", json=body)
    emit(
        eng,
        json_mode=ctx.obj.json_mode,
        table=kv_table(
            f"Created engagement {eng['slug']!r}",
            [("id", eng["id"]), ("slug", eng["slug"]), ("name", eng["name"]),
             ("status", eng["status"]), ("created_at", eng["created_at"])],
        ),
    )


@engagement_group.command("view")
@click.argument("slug")
@click.pass_context
def view(ctx: click.Context, slug: str) -> None:
    """Read one engagement by slug."""
    with ctx.obj.client() as c:
        eng = c.get(f"/engagements/{slug}")
    emit(
        eng,
        json_mode=ctx.obj.json_mode,
        table=kv_table(f"{slug}", [(k, eng.get(k)) for k in
                                   ["id", "slug", "name", "status", "created_at"]]),
    )


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

@engagement_group.group("scope")
def scope_group() -> None:
    """Manage scope items for an engagement."""


@scope_group.command("list")
@click.argument("slug")
@click.pass_context
def scope_list(ctx: click.Context, slug: str) -> None:
    """List scope items on engagement SLUG."""
    with ctx.obj.client() as c:
        rows = c.get(f"/engagements/{slug}/scope")
    t = Table(title=f"Scope ({slug})")
    t.add_column("kind", style="bold")
    t.add_column("value")
    t.add_column("exclude", justify="center")
    t.add_column("note")
    for r in rows:
        t.add_row(r["kind"], r["value"], "x" if r["is_exclusion"] else "", r.get("note") or "")
    emit(rows, json_mode=ctx.obj.json_mode, table=t)


@scope_group.command("add")
@click.argument("slug")
@click.option("--kind", required=True,
              type=click.Choice(["domain", "subdomain", "cidr", "ip", "url", "email"]),
              help="Scope item kind.")
@click.option("--value", required=True, help="Target value (e.g. 'acme.com', '10.0.0.0/24').")
@click.option("--exclude", is_flag=True,
              help="Mark as an exclusion — skipped even if covered by an include item.")
@click.option("--note", help="Free-form note (audit log only).")
@click.pass_context
def scope_add(
    ctx: click.Context,
    slug: str,
    kind: str,
    value: str,
    exclude: bool,
    note: str | None,
) -> None:
    """Add a scope item to engagement SLUG."""
    body: dict[str, object] = {"kind": kind, "value": value, "is_exclusion": exclude}
    if note:
        body["note"] = note
    with ctx.obj.client() as c:
        item = c.post(f"/engagements/{slug}/scope", json=body)
    emit(item, json_mode=ctx.obj.json_mode,
         table=kv_table("Scope item added",
                        [("id", item["id"]), ("kind", item["kind"]),
                         ("value", item["value"]), ("is_exclusion", item["is_exclusion"])]))


@scope_group.command("remove")
@click.argument("slug")
@click.argument("scope_id")
@click.pass_context
def scope_remove(ctx: click.Context, slug: str, scope_id: str) -> None:
    """Remove scope item SCOPE_ID from engagement SLUG."""
    with ctx.obj.client() as c:
        c.delete(f"/engagements/{slug}/scope/{scope_id}")
    from rtd.output import console
    console.print(f"removed scope item [bold]{scope_id}[/bold]")
