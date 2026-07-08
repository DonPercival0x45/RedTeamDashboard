"""Tool-invocation orchestrator (v0.12.0).

Callers pass a Tool row + args + acting user and get back a fully
persisted ToolInvocation row with captured outputs. This module owns:

- Picking the right :class:`SandboxRunner` based on
  ``settings.sandbox_runner`` (docker | aci).
- Args validation against the manifest spec (types, required, enum).
- Building the ``scope`` payload from the engagement's ScopeItem rows.
- Charter gate: agent-initiated invocations of ``task_kind=exploit``
  tools are blocked.
- Turning source bytes back into memory from the v0.11 placeholder
  ``validation.source_b64`` field.
- Persisting the running row → completed row on the same session, with
  timing + captured output stamped in.
"""
from __future__ import annotations

import base64
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    ActorType,
    AuditLog,
    Engagement,
    Entity,
    Finding,
    ScopeItem,
    Tool,
    ToolInvocation,
    ToolInvocationStatus,
    ToolKind,
    ToolStatus,
    ToolTaskKind,
    User,
)
from app.services.entities import extract_entities
from app.services.sandbox_local import LocalDockerRunner
from app.services.sandbox_runner import (
    RUNTIME_RATES_USD_PER_SECOND,
    SandboxRequest,
    SandboxRunner,
)


class ToolInvocationError(Exception):
    """Raised for pre-flight problems that block dispatch. Runtime
    failures land on the row instead (``status=failed`` + ``error``)."""


@lru_cache(maxsize=1)
def _pick_runner() -> SandboxRunner:
    if settings.sandbox_runner == "aci":
        from app.services.sandbox_aci import ACIRunner

        return ACIRunner()
    return LocalDockerRunner()


async def invoke_tool(
    session: Session,
    engagement: Engagement,
    tool: Tool,
    args: dict[str, Any],
    invoker: User,
    actor_type: ActorType = ActorType.user,
) -> ToolInvocation:
    """Kick a tool invocation end-to-end. Returns the persisted row
    with terminal status set (completed / failed / timeout).

    Blocks if:
    - Tool is not ``approved``.
    - ``task_kind=exploit`` and ``actor_type != user`` (charter gate:
      agents can only dispatch enum/scan tools; exploit is analyst-only).
    - Required arg missing / wrong type.
    """
    if tool.status != ToolStatus.approved:
        raise ToolInvocationError(
            f"tool '{tool.name}' is {tool.status.value}, not approved — "
            "invoke blocked"
        )
    if (
        tool.task_kind == ToolTaskKind.exploit
        and actor_type != ActorType.user
    ):
        raise ToolInvocationError(
            "exploit-kind tools are analyst-only (charter). Agent dispatch blocked."
        )

    validated_args = _validate_args(tool, args)
    scope = _build_scope(session, engagement)
    entities = _build_entities(session, engagement)
    source_bytes = _decode_source(tool)

    # v0.15.0: shared_kali_box runtime lands in v0.17. Refuse it
    # up-front so callers see a clean error instead of the sandbox
    # runner reaching for a code path that doesn't exist yet.
    manifest_runtime = (
        (tool.manifest or {}).get("spec", {}).get("runtime", "fresh_container")
    )
    if manifest_runtime == "shared_kali_box":
        raise ToolInvocationError(
            "runtime='shared_kali_box' is not yet implemented — coming in "
            "v0.17. Use runtime='fresh_container' (the default) for now."
        )

    row = ToolInvocation(
        tool_id=tool.id,
        tool_version=tool.version,
        engagement_id=engagement.id,
        invoker_user_id=invoker.id,
        args=validated_args,
        status=ToolInvocationStatus.running,
    )
    session.add(row)
    session.flush()  # so row.id is set before we hand it to the runner

    runner = _pick_runner()
    manifest_spec = (tool.manifest or {}).get("spec", {}) or {}
    python_deps = _resolve_python_deps(tool, manifest_spec)
    req = SandboxRequest(
        tool_id=str(tool.id),
        tool_name=tool.name,
        tool_version=tool.version,
        tool_kind=tool.kind.value,
        entrypoint=manifest_spec.get("entrypoint", "main.py"),
        source_bytes=source_bytes,
        python_deps=python_deps,
        args=validated_args,
        scope=scope,
        entities=entities,
        invocation_id=str(row.id),
        timeout_seconds=int(manifest_spec.get("timeout_seconds", 120)),
        allow_network=(
            manifest_spec.get("network_egress", ["none"]) != ["none"]
        ),
    )

    try:
        result = await runner.run(req)
    except Exception as exc:  # noqa: BLE001 — infra failure lands on the row
        row.status = ToolInvocationStatus.failed
        row.error = f"runner {runner.name} raised: {exc}"
        row.completed_at = datetime.now(tz=UTC)
        session.add(_audit(engagement, tool, row, invoker, "tool.invocation_failed"))
        session.commit()
        return row

    row.exit_code = result.exit_code
    row.stdout = result.stdout
    row.stderr = result.stderr
    row.runtime_ref = result.runtime_ref
    row.error = result.error
    row.completed_at = datetime.now(tz=UTC)
    # v0.15.0: stamp per-invocation compute cost. Rate is picked by
    # runner name (docker=$0, aci≈$2e-5/s); duration comes from the
    # runner's own wall-clock. Rough enough to seed the Costs tab
    # without pretending to be a billing source of truth.
    rate = RUNTIME_RATES_USD_PER_SECOND.get(runner.name, 0.0)
    row.cost_usd = Decimal(str(round(result.duration_seconds * rate, 6)))
    if result.timed_out:
        row.status = ToolInvocationStatus.timeout
    elif result.error is not None or (
        result.exit_code is not None and result.exit_code != 0
    ):
        row.status = ToolInvocationStatus.failed
    else:
        row.status = ToolInvocationStatus.completed

    session.add(_audit(engagement, tool, row, invoker, "tool.invocation_completed"))
    session.commit()
    return row


def _resolve_python_deps(tool: Tool, manifest_spec: dict[str, Any]) -> list[str]:
    """Return the pip package list to install before the tool runs.

    Prefers the manifest's declared ``python_deps``. Falls back to
    inferring from ``validation.ast.imports_seen`` — this backfills
    tools uploaded before v0.15.1, when Pydantic silently dropped the
    ``python_deps`` field because it wasn't declared on ``ToolSpec``.
    """
    declared = manifest_spec.get("python_deps") or []
    if declared:
        return list(declared)

    # Backfill path for pre-v0.15.1 tool rows.
    from app.services.tool_ast_check import infer_python_deps

    ast_result = (tool.validation or {}).get("ast") or {}
    imports_seen = ast_result.get("imports_seen") or []
    return infer_python_deps(imports_seen)


def _validate_args(tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
    """Cross-check ``args`` against the manifest's arg spec. Coerces
    integer / boolean strings when passed from HTML forms. Ignores
    unknown args rather than rejecting — v0.15 tightens if we care."""
    spec_args = ((tool.manifest or {}).get("spec", {}) or {}).get("args", []) or []
    validated: dict[str, Any] = {}
    for arg_spec in spec_args:
        name = arg_spec.get("name")
        if not name:
            continue
        provided = args.get(name)
        if provided is None:
            if arg_spec.get("required"):
                raise ToolInvocationError(f"missing required arg '{name}'")
            continue
        arg_type = arg_spec.get("type", "string")
        try:
            validated[name] = _coerce_arg(provided, arg_type, arg_spec)
        except (TypeError, ValueError) as exc:
            raise ToolInvocationError(
                f"arg '{name}' invalid: {exc}"
            ) from exc
    return validated


def _coerce_arg(value: Any, arg_type: str, arg_spec: dict[str, Any]) -> Any:
    if arg_type == "string":
        return str(value)
    if arg_type == "integer":
        return int(value)
    if arg_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if arg_type == "enum":
        allowed = arg_spec.get("values", []) or []
        v = str(value)
        if v not in allowed:
            raise ValueError(f"'{v}' not in enum values {allowed}")
        return v
    return value


def _build_scope(session: Session, engagement: Engagement) -> dict[str, Any]:
    rows = list(
        session.execute(
            select(ScopeItem).where(ScopeItem.engagement_id == engagement.id)
        ).scalars()
    )
    domains: list[str] = []
    ips: list[str] = []
    cidrs: list[str] = []
    urls: list[str] = []
    for r in rows:
        if r.is_exclusion:
            continue
        kind = r.kind.value if hasattr(r.kind, "value") else str(r.kind)
        if kind == "domain":
            domains.append(r.value)
        elif kind == "ip":
            ips.append(r.value)
        elif kind == "cidr":
            cidrs.append(r.value)
        elif kind == "url":
            urls.append(r.value)
    return {
        "engagement_slug": engagement.slug,
        "domains": domains,
        "ips": ips,
        "cidrs": cidrs,
        "urls": urls,
    }


def _build_entities(session: Session, engagement: Engagement) -> dict[str, list[str]]:
    """Group the engagement's discovered entities by type (v0.16.0).

    Mirrors what the analyst sees in the entities view: entities
    **derived from findings** (the primary source — emails/hosts/IPs
    extracted via :func:`extract_entities`) merged with **stored**
    entities (Maltego import). Entity ``type`` is free-form, so we group
    by whatever types actually exist rather than a fixed set — a future
    "IP reputation" tool reads ``entities['ip']``, a UPN enum reads
    ``entities['email']``, and types we don't know about yet still flow
    through under their own key. Values are deduped (derived wins on
    collision), order preserved (derived first, then stored).
    """
    grouped: dict[str, list[str]] = {}

    def _add(type_: str, value: str) -> None:
        bucket = grouped.setdefault(type_, [])
        val = value.strip()
        if val and val not in bucket:
            bucket.append(val)

    # 1. derived — computed from findings (Finding.target + content).
    findings = list(
        session.execute(
            select(Finding).where(
                Finding.engagement_id == engagement.id,
                Finding.deleted_at.is_(None),
            )
        ).scalars()
    )
    for e in extract_entities(findings):
        _add(e.get("type", ""), e.get("value", ""))

    # 2. stored — persisted via Maltego import / manual entry.
    stored = list(
        session.execute(
            select(Entity)
            .where(Entity.engagement_id == engagement.id)
            .order_by(Entity.type, Entity.value)
        ).scalars()
    )
    for r in stored:
        _add(r.type, r.value)

    return grouped


def _decode_source(tool: Tool) -> bytes | None:
    """Pull the source bytes back out of the v0.11 placeholder field
    (``tool.validation.source_b64``). Binary tools have no source."""
    if tool.kind == ToolKind.binary:
        return None
    b64 = (tool.validation or {}).get("source_b64")
    if not isinstance(b64, str) or not b64:
        raise ToolInvocationError(
            f"tool '{tool.name}' has no stored source (upgrade path?); "
            "re-upload the source file"
        )
    return base64.b64decode(b64)


def _audit(
    engagement: Engagement,
    tool: Tool,
    row: ToolInvocation,
    invoker: User,
    event: str,
) -> AuditLog:
    return AuditLog(
        engagement_id=engagement.id,
        actor_type=ActorType.user,
        actor_id=str(invoker.id),
        event_type=event,
        payload={
            "tool_id": str(tool.id),
            "tool_name": tool.name,
            "invocation_id": str(row.id),
            "status": row.status.value,
            "exit_code": row.exit_code,
        },
    )
