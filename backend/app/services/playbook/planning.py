"""Authoritative, hashable execution plans for playbook kickoff."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.models import Playbook
from app.services.playbook.executor import executor_for_tool_slug
from app.services.playbook.policy import MAX_PLAYBOOK_CALLS, recipe_requires_approval

_CREDENTIAL_TOOL = {
    "freeipapi": "freeipapi",
    "ipinfo": "ipinfo",
    "dehashed": "dehashed",
}
_ACTIVE_TOOLS = {
    "port_scan",
    "service_detect",
    "subnet_sweep",
    "mcp_port_scan",
    "mcp_service_detect",
    "mcp_subnet_sweep",
}
_PATH_LABEL = {"internal": "Built-in", "mcp": "Connected service"}


def credential_for_tool(tool_slug: str) -> str | None:
    return _CREDENTIAL_TOOL.get(tool_slug.removeprefix("mcp_"))


def build_execution_plan(
    *,
    playbook: Playbook,
    scope_subset: list[str],
    required_executor: str,
) -> dict[str, Any]:
    """Build the exact known plan plus explicit bounded dynamic expansion."""
    targets = [str(value).strip() for value in scope_subset]
    steps: list[dict[str, Any]] = []
    transports: set[str] = set()
    credentials: set[str] = set()
    dynamic = False
    minimum_calls = 0

    for step in playbook.steps:
        transport = executor_for_tool_slug(step.tool_slug)
        transports.add(transport)
        credential = credential_for_tool(step.tool_slug)
        if credential:
            credentials.add(credential)
        args_template = step.args_template or {}
        args_canonical = json.dumps(
            args_template,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        target_source = args_template.get("__target_source")
        expands_targets = bool(target_source)
        dynamic = dynamic or expands_targets
        minimum_calls += len(targets)
        steps.append(
            {
                "step_id": str(step.id),
                "sort_order": step.sort_order,
                "tool_slug": step.tool_slug,
                "description": step.description,
                "transport": transport,
                "risk": "active" if step.tool_slug in _ACTIVE_TOOLS else "passive",
                "credential": credential,
                "arguments_sha256": hashlib.sha256(args_canonical).hexdigest(),
                "coverage_node_ids": sorted(step.satisfies_node_ids or []),
                "target_count": len(targets),
                "expands_targets": expands_targets,
                "target_source": str(target_source) if target_source else None,
                "on_error": args_template.get("__on_error", "continue"),
            }
        )

    try:
        approval_required = playbook.active or recipe_requires_approval(
            step.tool_slug for step in playbook.steps
        )
    except ValueError:
        approval_required = playbook.active

    plan: dict[str, Any] = {
        "format_version": 1,
        "playbook_id": str(playbook.id),
        "playbook_slug": playbook.slug,
        "playbook_version": playbook.version,
        "playbook_name": playbook.name,
        "approval_required": approval_required,
        "required_executor": required_executor,
        "execution_paths": [
            _PATH_LABEL[kind] for kind in ("internal", "mcp") if kind in transports
        ],
        "required_credentials": sorted(credentials),
        "scope_subset": targets,
        "minimum_calls": minimum_calls,
        "maximum_calls": MAX_PLAYBOOK_CALLS if dynamic else minimum_calls,
        "dynamic_expansion": dynamic,
        "steps": steps,
        "safety_notes": [
            "Every target is validated against authoritative engagement scope before queueing.",
            "Current exclusions are rechecked immediately before each tool invocation.",
            *(
                [
                    "Authorized discoveries may add later calls; every derived "
                    "target is revalidated before execution."
                ]
                if dynamic
                else []
            ),
            *(
                ["This plan remains blocked until an analyst approves the run."]
                if approval_required
                else []
            ),
        ],
    }
    canonical = json.dumps(
        plan,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    plan["plan_sha256"] = hashlib.sha256(canonical).hexdigest()
    return plan
