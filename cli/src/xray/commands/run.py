"""`rtd run start` — kick off a run with an optional per-run LLM choice."""
from __future__ import annotations

import click

from xray.output import console, emit, kv_table


@click.group(name="run")
def run_group() -> None:
    """Start runs against an project."""


@run_group.command("start")
@click.argument("slug")
@click.option("--prompt", "-p", required=True, help="Prompt sent to the agent.")
@click.option("--provider",
              type=click.Choice(["anthropic", "openai", "azure", "ollama"]),
              help="LLM provider for this run. Omit to use the worker's default.")
@click.option("--model", "model_name",
              help="Model id (provider-specific). Required if --provider is set.")
@click.option("--tail/--no-tail", default=True,
              help="Stream events after start. Disabled if --json is set.")
@click.pass_context
def start(
    ctx: click.Context,
    slug: str,
    prompt: str,
    provider: str | None,
    model_name: str | None,
    tail: bool,
) -> None:
    """Start a run on project SLUG. Tails events by default."""
    if (provider is None) != (model_name is None):
        raise click.UsageError("--provider and --model must be given together (or both omitted).")

    body: dict[str, object] = {"prompt": prompt}
    if provider and model_name:
        body["model"] = {"provider": provider, "name": model_name}

    with ctx.obj.client() as c:
        result = c.post(f"/projects/{slug}/runs", json=body)

    emit(
        result,
        json_mode=ctx.obj.json_mode,
        table=kv_table(
            "Run started",
            [("thread_id", result["thread_id"]),
             ("model", f"{result['model']['provider']}/{result['model']['name']}"),
             ("events_stream", result["events_stream"])],
        ),
    )

    if tail and not ctx.obj.json_mode:
        console.print()
        # Avoid a circular import by reaching tail at call time.
        from xray.commands.tail import _tail_events
        _tail_events(ctx, slug, thread_id=result["thread_id"])
