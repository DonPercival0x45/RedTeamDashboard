"""v1.12.0: read-only catalog of the orchestrator's built-in MCP tools.

The ``tools`` DB catalog (``app.models.tool.Tool``) tracks analyst- and
admin-uploaded runtime tools with a manifest + LLM safety review. That
is a separate universe from the tools the Tactical agent dispatches
directly — ``subfinder``, ``dns_lookup``, ``whois_lookup``, ``crt_sh``,
``httpx_probe``, ``port_scan``, ``subnet_sweep``, ``service_detect``,
and their siblings live in ``app.orchestrator.tools`` and register as
``@mcp.tool()`` handlers on the FastMCP server. They ship with the
image; they are the "defaults" an analyst reaches for.

This endpoint surfaces that registry to the frontend so the Settings
banner and the Scope-tab "Current Tools" panel show real tools out of
the box, instead of the empty ``Tool`` table.

Read-only, any-authenticated-user. No mutations planned — the
authoritative registry is the source tree, not the API.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser
from app.orchestrator.tools import all_tools, phase_for_tool

router = APIRouter()


class OrchestratorToolRead(BaseModel):
    """One built-in orchestrator tool, projected for the UI.

    Two taxonomy fields ship together so the frontend can pick its
    grouping without a follow-up query:

    - ``kind`` is the charter task-kind gate (``enum`` / ``scan`` /
      ``exploit``). Agents can dispatch ``enum`` + ``scan``; ``exploit``
      is analyst-only. Same field the DB ``Tool.task_kind`` uses, so
      the frontend's existing ``TOOL_PHASES`` maps 1:1.
    - ``phase`` is the finding-phase this tool populates
      (``osint`` / ``vuln_scan`` / ``exploit`` / ``phishing`` /
      ``general``). More specific than ``kind`` when it comes to the
      actual work the tool does.
    """

    name: str
    description: str
    kind: str = Field(description="TaskKind: enum | scan | exploit")
    phase: str = Field(
        description="FindingPhase: osint | vuln_scan | exploit | phishing | general",
    )
    risk: str = Field(description="risk level: passive | active | destructive")
    target_arg: str = Field(
        description="name of the arg that holds the target (e.g. 'domain', 'host')",
    )
    example_prompt: str = Field(
        description="curated one-liner for the Scope-tab Current Tools panel",
    )


# Curated one-liner prompts for the built-in tools. Kept next to the
# API projection (not on the tool spec itself) so we can iterate the
# analyst-facing copy without editing the orchestrator source. Tool
# names not in this map fall back to a generic "Run <name> against an
# in-scope target." template client-side (see frontend/lib/tool-phases.ts).
_EXAMPLE_PROMPTS: dict[str, str] = {
    "subfinder": (
        "Enumerate subdomains for {target} via subfinder and probe what's live."
    ),
    "dns_lookup": "Resolve A, AAAA, MX, NS, and TXT records for {target}.",
    "whois_lookup": "Pull whois registration data for {target}.",
    "crt_sh": (
        "Query crt.sh for certificate-transparency records naming {target}."
    ),
    "reverse_dns": "Look up PTR records for {target}.",
    "httpx_probe": (
        "Probe {target} with httpx: status, title, tech fingerprint, and TLS chain."
    ),
    "port_scan": (
        "Scan common TCP ports on {target} and report open services."
    ),
    "subnet_sweep": (
        "Sweep {target} for live hosts and enumerate open TCP ports on responders."
    ),
    "service_detect": (
        "Fingerprint services on open ports for {target}."
    ),
}


def _example_for(spec: Any) -> str:
    template = _EXAMPLE_PROMPTS.get(spec.name)
    if template:
        # ``{target}`` placeholder — kept literal in the response so the
        # frontend can either fill from the engagement scope on click or
        # leave the analyst to edit. Cheaper than a per-engagement server
        # roundtrip.
        return template.replace("{target}", f"<{spec.target_arg}>")
    return f"Run {spec.name} against an in-scope target."


@router.get("/orchestrator/tools", response_model=list[OrchestratorToolRead])
def list_orchestrator_tools(_user: CurrentUser) -> list[OrchestratorToolRead]:
    """List every built-in MCP orchestrator tool.

    The registry is populated at import time from ``all_tools()``, so
    the response is a live view of what the current backend image
    exposes. No caching — the list is small (single-digit count today)
    and the source of truth is the running module, not persisted state.
    """
    out: list[OrchestratorToolRead] = []
    for spec in all_tools():
        out.append(
            OrchestratorToolRead(
                name=spec.name,
                description=spec.description or "",
                kind=spec.kind.value,
                phase=phase_for_tool(spec.name) or "general",
                risk=spec.risk.value,
                target_arg=spec.target_arg,
                example_prompt=_example_for(spec),
            )
        )
    # Stable ordering makes the UI reproducible across requests.
    out.sort(key=lambda t: (t.kind, t.name))
    return out
