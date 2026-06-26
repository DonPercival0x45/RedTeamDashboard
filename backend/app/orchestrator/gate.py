"""Scope gate + approval gate — the authorization spine.

Two composed pure functions:

- ``scope_check(spec, tool_args, scope_items)`` decides whether the tool's target
  falls inside the Project's scope. Exclusions beat includes.
- ``approval_check(spec, scope, authorization_id=...)`` decides whether the call
  auto-approves, needs human interrupt, or is denied.

Neither function touches the DB or the LangGraph runtime. Callers (graph nodes,
API endpoints) load scope items and persist Approval rows; the gate just
computes the decision.

Phase 0 note: every wired tool is passive, so the interrupt branch is
exercised only via tests until active tooling lands. ``authorization_id`` is
plumbed end-to-end but not yet honored — pre-authorized playbook lookup is
deferred along with the active tool set.
"""
from __future__ import annotations

import enum
import ipaddress
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.models import RiskLevel, ScopeKind
from app.orchestrator.scope import ScopeSnapshot
from app.orchestrator.tools import ToolSpec, get_tool


class Action(enum.StrEnum):
    auto = "auto"
    interrupt = "interrupt"
    deny = "deny"


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    ok: bool
    reason: str
    target: str | None = None
    matched_include_id: uuid.UUID | None = None
    matched_exclusion_id: uuid.UUID | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "target": self.target,
            "matched_include_id": (
                str(self.matched_include_id) if self.matched_include_id else None
            ),
            "matched_exclusion_id": (
                str(self.matched_exclusion_id) if self.matched_exclusion_id else None
            ),
        }


@dataclass(frozen=True, slots=True)
class Decision:
    action: Action
    reason: str
    scope: ScopeDecision
    risk: RiskLevel | None = None
    authorization_id: uuid.UUID | None = None

    @property
    def auto(self) -> bool:
        return self.action is Action.auto

    @property
    def requires_interrupt(self) -> bool:
        return self.action is Action.interrupt

    @property
    def denied(self) -> bool:
        return self.action is Action.deny


def _normalize_domain(value: str) -> str:
    return value.strip().lower().rstrip(".")


def _domain_matches(target: str, scope_value: str) -> bool:
    t = _normalize_domain(target)
    s = _normalize_domain(scope_value)
    if not t or not s:
        return False
    # Exact match, or target is a subdomain of scope. The leading dot guards
    # the label boundary so `evilacme.com` does NOT match `acme.com`.
    return t == s or t.endswith("." + s)


def _extract_host(url: str) -> str | None:
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    return parsed.hostname


def _ip_equals(a: str, b: str) -> bool:
    try:
        return ipaddress.ip_address(a.strip()) == ipaddress.ip_address(b.strip())
    except ValueError:
        return False


def _ip_in_cidr(ip_value: str, cidr_value: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_value.strip())
        net = ipaddress.ip_network(cidr_value.strip(), strict=False)
    except ValueError:
        return False
    return ip in net


def _cidr_subnet_of(target_cidr: str, scope_cidr: str) -> bool:
    try:
        tnet = ipaddress.ip_network(target_cidr.strip(), strict=False)
        snet = ipaddress.ip_network(scope_cidr.strip(), strict=False)
    except ValueError:
        return False
    if tnet.version != snet.version:
        return False
    return tnet.subnet_of(snet)


def _item_matches(target: str, target_kind: ScopeKind, item: ScopeSnapshot) -> bool:
    if target_kind is ScopeKind.domain:
        if item.kind is ScopeKind.domain:
            return _domain_matches(target, item.value)
        return False

    if target_kind is ScopeKind.url:
        host = _extract_host(target)
        if host is None:
            return False
        if item.kind is ScopeKind.url:
            return target.strip().lower() == item.value.strip().lower()
        if item.kind is ScopeKind.domain:
            return _domain_matches(host, item.value)
        return False

    if target_kind is ScopeKind.ip:
        if item.kind is ScopeKind.ip:
            return _ip_equals(target, item.value)
        if item.kind is ScopeKind.cidr:
            return _ip_in_cidr(target, item.value)
        return False

    if target_kind is ScopeKind.cidr:
        if item.kind is ScopeKind.cidr:
            return _cidr_subnet_of(target, item.value)
        return False

    return False


def scope_check(
    spec: ToolSpec,
    tool_args: Mapping[str, Any],
    scope_items: Sequence[ScopeSnapshot],
) -> ScopeDecision:
    raw = tool_args.get(spec.target_arg)
    if raw is None:
        return ScopeDecision(
            ok=False,
            reason=f"missing target arg '{spec.target_arg}'",
        )
    if isinstance(raw, list):
        # The dispatch node fans these out; if we still see one here it means
        # the caller didn't expand. Surface it clearly so the agent corrects.
        return ScopeDecision(
            ok=False,
            reason=(
                f"target arg '{spec.target_arg}' is a list; call this tool "
                "once per target instead of batching"
            ),
        )
    if not isinstance(raw, str) or not raw.strip():
        return ScopeDecision(
            ok=False,
            reason=f"empty target arg '{spec.target_arg}'",
        )
    target = raw.strip()

    # Exclusions beat includes — check them first.
    for item in scope_items:
        if item.is_exclusion and _item_matches(target, spec.kind, item):
            return ScopeDecision(
                ok=False,
                reason=f"target {target!r} matches exclusion {item.value!r}",
                target=target,
                matched_exclusion_id=item.id,
            )

    for item in scope_items:
        if not item.is_exclusion and _item_matches(target, spec.kind, item):
            return ScopeDecision(
                ok=True,
                reason=f"target {target!r} matches scope item {item.value!r}",
                target=target,
                matched_include_id=item.id,
            )

    return ScopeDecision(
        ok=False,
        reason=f"target {target!r} not in any scope item",
        target=target,
    )


def approval_check(
    spec: ToolSpec,
    scope: ScopeDecision,
    *,
    authorization_id: uuid.UUID | None = None,
) -> Decision:
    if not scope.ok:
        return Decision(
            action=Action.deny,
            reason=scope.reason,
            scope=scope,
            risk=spec.risk,
            authorization_id=authorization_id,
        )
    if spec.risk is RiskLevel.passive:
        return Decision(
            action=Action.auto,
            reason="passive tool, target in scope",
            scope=scope,
            risk=spec.risk,
            authorization_id=authorization_id,
        )
    # active / destructive: normally a human interrupt, but a standing session
    # grant (authorization_id) for this tool auto-approves it. The auto-approval
    # is still recorded against the authorization id by the caller.
    if authorization_id is not None:
        return Decision(
            action=Action.auto,
            reason=f"{spec.risk.value} tool covered by session authorization",
            scope=scope,
            risk=spec.risk,
            authorization_id=authorization_id,
        )
    return Decision(
        action=Action.interrupt,
        reason=f"{spec.risk.value} tool requires human approval",
        scope=scope,
        risk=spec.risk,
        authorization_id=authorization_id,
    )


def evaluate(
    tool_name: str,
    tool_args: Mapping[str, Any],
    scope_items: Sequence[ScopeSnapshot],
    *,
    authorization_id: uuid.UUID | None = None,
    registry: Mapping[str, ToolSpec] | None = None,
) -> Decision:
    spec = get_tool(tool_name, registry=registry)
    if spec is None:
        return Decision(
            action=Action.deny,
            reason=f"unknown tool: {tool_name!r}",
            scope=ScopeDecision(ok=False, reason="tool not in registry"),
            risk=None,
            authorization_id=authorization_id,
        )
    scope = scope_check(spec, tool_args, scope_items)
    return approval_check(spec, scope, authorization_id=authorization_id)
