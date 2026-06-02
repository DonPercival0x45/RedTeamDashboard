"""`rtd approve` — decide a pending approval."""
from __future__ import annotations

import click

from rtd.output import console, emit, kv_table


@click.command()
@click.argument("approval_id")
@click.option("--deny", is_flag=True, help="Deny instead of approving (default is approve).")
@click.option("--remember", is_flag=True,
              help="Approve once + grant a session authorization for the same tool.")
@click.option("--reason", help="Free-form note (audit log only).")
@click.option("--edit", "edited_args", multiple=True, metavar="KEY=VALUE",
              help="Override one or more tool args (repeatable).")
@click.pass_context
def approve(
    ctx: click.Context,
    approval_id: str,
    deny: bool,
    remember: bool,
    reason: str | None,
    edited_args: tuple[str, ...],
) -> None:
    """Approve (default) or deny the pending approval APPROVAL_ID."""
    if deny and remember:
        raise click.UsageError("--remember can't be combined with --deny.")

    body: dict[str, object] = {"approved": not deny}
    if remember:
        body["remember_for_session"] = True
    if reason:
        body["reason"] = reason
    if edited_args:
        edits: dict[str, str] = {}
        for raw in edited_args:
            if "=" not in raw:
                raise click.UsageError(f"--edit needs KEY=VALUE; got {raw!r}")
            k, _, v = raw.partition("=")
            edits[k.strip()] = v.strip()
        body["edited_args"] = edits

    with ctx.obj.client() as c:
        result = c.post(f"/approvals/{approval_id}/decision", json=body)

    status = result["status"]
    color = "green" if status == "approved" else "blue" if status == "edited" else "red"
    console.print(f"approval [{color}]{status}[/{color}]: {approval_id}")
    emit(result, json_mode=ctx.obj.json_mode,
         table=kv_table("Decision", [(k, result.get(k)) for k in
                                     ["status", "tool_name", "decided_at"]]))
