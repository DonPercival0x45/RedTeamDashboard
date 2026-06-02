"""`rtd` — top-level click group + per-command registration.

Global flags live here (``--profile``, ``--config``, ``--json``). Subcommands
read the resolved config + client off ``click.get_current_context().obj``.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import click

from rtd import __version__
from rtd.client import APIError, Client
from rtd.commands import (
    approve,
    engagement,
    findings,
    grants,
    login,
    profile,
    run,
    ssh,
    tail,
)
from rtd.config import Config, ConfigError
from rtd.output import error


@dataclass
class Context:
    """Carrier for everything the subcommand needs.

    A subcommand that needs an HTTP client calls ``ctx.client()``; the lazy
    construction keeps ``rtd --help`` and ``rtd login`` working without an
    existing profile.
    """

    config: Config
    profile_name: str | None
    json_mode: bool

    def client(self) -> Client:
        return Client(self.config.resolve(self.profile_name))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="rtd")
@click.option(
    "--profile",
    "profile_name",
    metavar="NAME",
    help="Use this named profile instead of the default.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Alternate config file location.",
)
@click.option(
    "--json",
    "json_mode",
    is_flag=True,
    help="Emit JSON to stdout instead of human-readable tables.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    profile_name: str | None,
    config_path: Path | None,
    json_mode: bool,
) -> None:
    """Red Team Dashboard CLI."""
    try:
        cfg = Config.load(config_path)
    except ConfigError as exc:
        error(str(exc))
        sys.exit(2)
    ctx.obj = Context(config=cfg, profile_name=profile_name, json_mode=json_mode)


# Top-level command registration. One import per file keeps churn local
# when commands move around.
cli.add_command(login.login)
cli.add_command(profile.profile_group)
cli.add_command(engagement.engagement_group)
cli.add_command(run.run_group)
cli.add_command(approve.approve)
cli.add_command(tail.tail)
cli.add_command(grants.grants_group)
cli.add_command(findings.findings_group)
cli.add_command(ssh.ssh)


def main_entrypoint() -> None:
    """Invoke the CLI, translating expected errors to clean stderr + non-zero exit."""
    try:
        cli(standalone_mode=False)
    except click.exceptions.Abort:
        sys.exit(130)
    except click.exceptions.UsageError as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except APIError as exc:
        error(f"{exc.detail} (HTTP {exc.status_code})")
        sys.exit(1)
    except ConfigError as exc:
        error(str(exc))
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main_entrypoint()
