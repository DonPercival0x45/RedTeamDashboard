"""`rtd login` — write/update a profile on disk."""
from __future__ import annotations

import click

from xray.config import Profile
from xray.output import console


@click.command()
@click.option("--profile", "profile_name", required=True, metavar="NAME",
              help="Profile name (e.g. 'personal', 'work').")
@click.option("--url", required=True, metavar="URL",
              help="Backend base URL, e.g. https://rtd-prod-backend.<env>.<region>.azurecontainerapps.io")
@click.option("--key", "api_key", required=True, metavar="RTD_KEY",
              help="API key minted by `python -m app.scripts.mint_api_key` (starts with rtd_).")
@click.option("--default/--no-default", default=False,
              help="Mark this profile as the default (always done for the first profile).")
@click.pass_context
def login(
    ctx: click.Context,
    profile_name: str,
    url: str,
    api_key: str,
    default: bool,
) -> None:
    """Save (or update) a profile and its API key locally."""
    cfg = ctx.obj.config
    cfg.upsert(Profile(name=profile_name, url=url.rstrip("/"), api_key=api_key),
               make_default=default)
    cfg.save()
    is_default = cfg.default == profile_name
    suffix = " (default)" if is_default else ""
    console.print(f"saved profile [bold]{profile_name}[/bold]{suffix} -> {cfg.path}")
