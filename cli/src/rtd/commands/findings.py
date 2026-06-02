"""`rtd findings list` — read persisted findings for an engagement."""
from __future__ import annotations

import click
from rich.table import Table

from rtd.output import emit


@click.group(name="findings")
def findings_group() -> None:
    """Read findings produced by past runs."""


@findings_group.command("list")
@click.argument("slug")
@click.option("--severity",
              type=click.Choice(["info", "low", "medium", "high", "critical"]),
              help="Filter by minimum severity.")
@click.pass_context
def list_findings(ctx: click.Context, slug: str, severity: str | None) -> None:
    """List findings on engagement SLUG, highest severity first."""
    with ctx.obj.client() as c:
        rows = c.get(f"/engagements/{slug}/findings")

    if severity:
        order = ["info", "low", "medium", "high", "critical"]
        min_rank = order.index(severity)
        rows = [r for r in rows if order.index(r.get("severity", "info")) >= min_rank]

    t = Table(title=f"Findings ({slug})")
    t.add_column("severity", style="bold")
    t.add_column("title")
    t.add_column("target")
    t.add_column("tool")
    t.add_column("created")
    for r in rows:
        t.add_row(
            _color_sev(r.get("severity", "info")),
            r.get("title", ""),
            r.get("target", "") or "",
            r.get("tool", "") or "",
            (r.get("created_at") or "")[:19],
        )
    emit(rows, json_mode=ctx.obj.json_mode, table=t)


_SEV_COLOR = {"critical": "red", "high": "bright_red", "medium": "yellow",
              "low": "blue", "info": "dim"}


def _color_sev(sev: str) -> str:
    color = _SEV_COLOR.get(sev, "white")
    return f"[{color}]{sev}[/{color}]"
