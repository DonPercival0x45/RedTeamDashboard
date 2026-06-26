"""`rtd ssh` — shell into the deployed backend Container App.

This is the one command that bypasses the HTTP surface: it delegates to
``az containerapp exec``, which the operator already has from running the
deployment kit. ``rtd ssh <slug>`` is just sugar that maps an project slug
(or the profile's deployment by default) to the underlying app name.

For the MVP we resolve "<env>-backend" from the profile's URL hostname —
e.g. ``rtd-prod-backend.purplebeach-xxx.centralus.azurecontainerapps.io``
becomes app ``rtd-prod-backend`` in resource group ``rtd-prod``.
"""
from __future__ import annotations

import os
import re
import sys

import click

from xray.output import console, error

_HOSTNAME_RE = re.compile(r"^(?P<app>[a-z0-9][a-z0-9-]+)\.")


@click.command()
@click.option("--resource-group", "-g",
              help="Override the Azure resource group (defaults to <appname-without-suffix>).")
@click.option("--command", default="/bin/bash",
              help="Command to run inside the container (defaults to an interactive shell).")
@click.pass_context
def ssh(ctx: click.Context, resource_group: str | None, command: str) -> None:
    """Open an interactive shell in the deployment's backend Container App.

    Requires the ``az`` CLI installed and logged into the target subscription.
    """
    profile = ctx.obj.config.resolve(ctx.obj.profile_name)
    app = _app_from_url(profile.url)
    if app is None:
        error(f"could not derive a Container App name from {profile.url!r}")
        sys.exit(1)
    rg = resource_group or _default_rg(app)

    console.print(f"[dim]exec into [bold]{app}[/bold] in [bold]{rg}[/bold]...[/dim]")
    # `os.execvp` is right here: we want stdin/stdout/tty to pass through
    # cleanly to az's interactive websocket. No reason to keep `rtd` in the
    # process tree once az takes over.
    os.execvp(
        "az",
        ["az", "containerapp", "exec", "-n", app, "-g", rg, "--command", command],
    )


def _app_from_url(url: str) -> str | None:
    """Strip scheme + take the first DNS label.

    ``https://rtd-prod-backend.purplebeach-xx.centralus.azurecontainerapps.io``
        -> ``rtd-prod-backend``
    """
    cleaned = re.sub(r"^https?://", "", url, count=1).strip("/")
    m = _HOSTNAME_RE.match(cleaned)
    return m.group("app") if m else None


def _default_rg(app: str) -> str:
    """Strip the trailing ``-backend`` (or whatever role suffix) to recover the RG."""
    # Kit convention: RG is ``rtd-<env>``, apps are ``rtd-<env>-{backend,worker,redis}``.
    parts = app.rsplit("-", 1)
    if len(parts) == 2 and parts[1] in {"backend", "worker", "redis"}:
        return parts[0]
    return app
