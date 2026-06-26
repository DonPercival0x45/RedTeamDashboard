"""`rtd profile ...` — manage saved profiles without re-entering credentials."""
from __future__ import annotations

import click
from rich.table import Table

from xray.config import ConfigError
from xray.output import console, emit


@click.group(name="profile")
def profile_group() -> None:
    """List, switch, or remove saved profiles."""


@profile_group.command("list")
@click.pass_context
def list_profiles(ctx: click.Context) -> None:
    """Show every saved profile; mark the default with `*`."""
    cfg = ctx.obj.config
    payload = [
        {"name": p.name, "url": p.url, "default": p.name == cfg.default}
        for p in cfg.profiles.values()
    ]
    if not payload:
        if ctx.obj.json_mode:
            emit([], json_mode=True)
        else:
            console.print("[dim]no profiles yet. Run `rtd login` to add one.[/dim]")
        return

    t = Table(title="Profiles")
    t.add_column("", width=2)
    t.add_column("name", style="bold")
    t.add_column("url")
    for row in payload:
        marker = "*" if row["default"] else " "
        t.add_row(marker, row["name"], row["url"])
    emit(payload, json_mode=ctx.obj.json_mode, table=t)


@profile_group.command("use")
@click.argument("name")
@click.pass_context
def use(ctx: click.Context, name: str) -> None:
    """Set NAME as the default profile."""
    cfg = ctx.obj.config
    if name not in cfg.profiles:
        raise ConfigError(f"no such profile: {name!r}")
    cfg.default = name
    cfg.save()
    console.print(f"default profile is now [bold]{name}[/bold]")


@profile_group.command("remove")
@click.argument("name")
@click.confirmation_option(prompt="Really delete this profile?")
@click.pass_context
def remove(ctx: click.Context, name: str) -> None:
    """Delete a profile (asks first)."""
    cfg = ctx.obj.config
    cfg.remove(name)
    cfg.save()
    console.print(f"removed profile [bold]{name}[/bold]")
