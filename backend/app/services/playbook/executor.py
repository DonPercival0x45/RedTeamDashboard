"""Playbook executor protocol + internal implementation.

The runner does NOT invoke tools directly. It calls
``PlaybookExecutor.run_step``, which is where "how the tool actually runs"
lives — internal (in-process) or MCP (out-of-process). Same signature, two
implementations; the runner is agnostic.

A3a ships:
* ``PlaybookExecutor`` — the ``Protocol`` interface.
* ``StepResult`` — the return-value dataclass every executor produces.
* ``InternalExecutor`` — the in-process impl. **A3a is the seam, not the wire
  to the tool registry** — the ``run_step`` implementation is a NotImplemented
  stub. Real wiring to ``services.tool_invocation`` lands in A3b. This lets us
  ship the runner + coverage-writing surface reviewable on its own and use a
  ``MockExecutor`` in tests to prove the seam.

A4 adds ``MCPExecutor`` implementing the same protocol. The playbook run
row now carries an ``executor_kind`` column so the worker picks between
internal (in-process) and MCP (out-of-process) per-run.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StepResult:
    """The runner-facing outcome of one tool invocation.

    Executors return this. The runner uses:

    * ``ok`` — did the tool succeed? Drives ``CoverageRecord.status`` +
      ``PlaybookRun.steps_succeeded/failed`` counters.
    * ``findings_*`` — accumulate into ``PlaybookRun`` and populate the
      ``FindingsSummary`` in the ``collection.job.completed`` milestone.
      Executors compute these deterministically (counts, not summaries).
    * ``error`` — populated iff ``ok is False``; short human-readable
      message. Stored on the coverage record's ``notes``.
    * ``data`` — freeform bag the executor may return for diagnostics /
      audit. Runner treats it as opaque; not persisted in A3a.
    """

    ok: bool
    findings_new: int = 0
    findings_unvalidated: int = 0
    findings_high_severity: int = 0
    findings_total: int = 0
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class PlaybookExecutor(Protocol):
    """Interface every executor honors.

    ``scope_context`` = the analyst-declared scope item the step is running
    against (a single scope_item_id — the runner iterates over the run's
    ``scope_subset`` and calls ``run_step`` per item per step). Executors
    substitute ``{{scope_item}}`` in the args template with this value.
    """

    def run_step(
        self,
        *,
        tool_slug: str,
        args_template: Mapping[str, Any],
        scope_context: str,
    ) -> StepResult:
        ...


ToolCallable = Callable[[str, dict[str, Any]], StepResult]


def _default_registry() -> dict[str, ToolCallable]:
    """The A3b tool dispatch table.

    Imported lazily so the executor module doesn't drag in dnspython on
    every ``from executor import ...`` — the tools package resolves
    dnspython + python-whois at first use, not at import time.
    """
    from app.services.playbook import tools as _tools

    return {
        "dns-inventory": _tools.run_dns_inventory,
        "whois": _tools.run_whois,
        "subfinder": _tools.run_subfinder,
        "crtsh": _tools.run_crtsh,
        "breach-lookup": _tools.run_breach_lookup,
    }


class InternalExecutor:
    """In-process executor — Track A step A3b.

    Dispatches by ``tool_slug`` through a plain dict registry. Unknown slugs
    become ``StepResult(ok=False)`` so a playbook referencing a not-yet-
    implemented tool degrades to a step failure instead of crashing the run.

    Callers can override the registry (tests, custom deployments) by passing
    ``registry=...``; the default table wires the OSINT playbook's five
    tools (two real, three stubs — see ``services/playbook/tools/``).
    """

    def __init__(self, registry: dict[str, ToolCallable] | None = None) -> None:
        self._registry: dict[str, ToolCallable] = (
            registry if registry is not None else _default_registry()
        )

    def register(self, tool_slug: str, fn: ToolCallable) -> None:
        """Add / replace a tool at runtime. Used by extension points that
        want to ship a new playbook tool without editing the executor."""
        self._registry[tool_slug] = fn

    def run_step(
        self,
        *,
        tool_slug: str,
        args_template: Mapping[str, Any],
        scope_context: str,
    ) -> StepResult:
        fn = self._registry.get(tool_slug)
        if fn is None:
            return StepResult(
                ok=False,
                error=f"unknown tool: {tool_slug!r}",
            )
        args = substitute_scope(args_template, scope_context)
        return fn(scope_context, args)


def substitute_scope(
    args_template: Mapping[str, Any], scope_context: str
) -> dict[str, Any]:
    """Replace ``{{scope_item}}`` in string values of the args template.

    Kept as a plain function so the mock executor can reuse the same
    substitution the internal executor will. Non-string values pass through
    untouched. Nested dicts/lists are traversed shallowly — playbook step
    args templates are flat by convention in A3a.
    """
    out: dict[str, Any] = {}
    for k, v in args_template.items():
        if isinstance(v, str):
            out[k] = v.replace("{{scope_item}}", scope_context)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# MCPExecutor — A4
# ---------------------------------------------------------------------------


class MCPExecutor:
    """Playbook executor that dispatches to the MCP server — Track A step A4.

    Same ``PlaybookExecutor`` protocol as ``InternalExecutor``; different
    transport. Constructs a lazy ``MultiServerMCPClient`` (from
    ``langchain-mcp-adapters``, the same client the worker uses at
    ``app/worker/mcp_executor.py``) so an executor instance can serve many
    ``run_step`` calls without re-handshaking per call. The tool catalog is
    fetched once on first invocation and cached for the executor's lifetime;
    a fresh MCPExecutor per run picks up newly-registered tools.

    Auth: analyst-facing analysts run playbooks; auth reuses the worker's
    CLI-scoped API key (``settings.worker_mcp_api_key``). ``lease_token`` is
    optional in A4 v0 — the worker MCP endpoint accepts CLI keys without a
    lease for the current catalogue.

    Coercion (``_coerce_response``): the MCP wire returns content-parts,
    which we walk down to the tool's structured payload. Findings counts
    come from an ``_lease_findings`` list when the tool wrote findings, or
    fall through to 0 when the response is bulk-data-only (like ipinfo). A
    top-level ``"error"`` key flips ``ok=False``.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        lease_token: str | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._lease_token = lease_token
        self._client: Any | None = None
        self._tool_cache: dict[str, Any] = {}

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from langchain_mcp_adapters.client import MultiServerMCPClient

        headers = {"X-API-Key": self._api_key}
        if self._lease_token:
            headers["X-Lease-Token"] = self._lease_token
        self._client = MultiServerMCPClient(
            {
                "rtd": {
                    "url": self._base_url,
                    "transport": "sse",
                    "headers": headers,
                }
            }
        )
        return self._client

    async def _load_tools(self) -> None:
        if self._tool_cache:
            return
        tools = await self._get_client().get_tools()
        for tool in tools:
            self._tool_cache[tool.name] = tool

    async def _ainvoke(self, name: str, args: Mapping[str, Any]) -> Any:
        await self._load_tools()
        tool = self._tool_cache.get(name)
        if tool is None:
            raise KeyError(name)
        return await tool.ainvoke(dict(args))

    def run_step(
        self,
        *,
        tool_slug: str,
        args_template: Mapping[str, Any],
        scope_context: str,
    ) -> StepResult:
        args = substitute_scope(args_template, scope_context)
        try:
            raw = asyncio.run(self._ainvoke(tool_slug, args))
        except KeyError:
            return StepResult(
                ok=False,
                error=f"MCP server does not expose tool {tool_slug!r}",
            )
        except BaseException as exc:  # noqa: BLE001 - MCP transport errors are step failures
            detail = _unwrap_exception_detail(exc)
            logger.exception(
                "playbook.mcp_executor_failed",
                tool=tool_slug,
                error=detail,
            )
            return StepResult(
                ok=False,
                error=f"mcp transport error ({type(exc).__name__}): {detail}",
            )
        return _coerce_response(raw)


def _coerce_response(raw: Any) -> StepResult:
    """Normalize an MCP tool response into ``StepResult``.

    Mirrors the worker's ``_coerce_tool_response`` (v1.4.8) but returns a
    ``StepResult``: findings_new/findings_total come from ``_lease_findings``
    when present, otherwise 0. A top-level ``"error"`` key flips ``ok=False``.
    Empty-but-successful responses (bulk-data-only tools like ipinfo)
    surface as ``ok=True`` with 0 findings — the technique ran, that's
    what coverage cares about.
    """
    raw = _unwrap_content_parts(raw)

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return StepResult(ok=True, data={"raw": raw})

    if not isinstance(raw, Mapping):
        return StepResult(ok=True, data={"value": raw})

    if "error" in raw:
        return StepResult(ok=False, error=str(raw.get("error")))

    findings = raw.get("_lease_findings")
    findings_total = len(findings) if isinstance(findings, list) else 0
    data = {k: v for k, v in raw.items() if k != "_lease_findings"}
    return StepResult(
        ok=True,
        findings_new=findings_total,
        findings_total=findings_total,
        data=data,
    )


def _unwrap_content_parts(raw: Any) -> Any:
    """Peel the MCP wire wrapper off ``raw`` so downstream code sees the
    tool's raw JSON dict. Copied from ``worker/mcp_executor.py`` because
    the wire format is a langchain-mcp-adapters concern that both executor
    call sites need to handle identically."""
    structured = getattr(raw, "structuredContent", None)
    if isinstance(structured, Mapping):
        return dict(structured)

    content = getattr(raw, "content", None)
    if isinstance(content, list):
        raw = content

    if isinstance(raw, list):
        text_chunks: list[str] = []
        for part in raw:
            txt = (
                part.get("text")
                if isinstance(part, Mapping)
                else getattr(part, "text", None)
            )
            if isinstance(txt, str):
                text_chunks.append(txt)
        if text_chunks:
            joined = "".join(text_chunks)
            try:
                return json.loads(joined)
            except (ValueError, TypeError):
                return joined

    return raw


def _unwrap_exception_detail(exc: BaseException, depth: int = 0) -> str:
    """Walk a possibly-nested exception (BaseExceptionGroup + __cause__ /
    __context__) so the analyst sees WHAT actually broke instead of the
    wrapper. Same behavior as the worker's helper."""
    if depth > 5:
        return str(exc) or type(exc).__name__

    inner = getattr(exc, "exceptions", None)
    if inner:
        parts = [
            f"{type(sub).__name__}: {_unwrap_exception_detail(sub, depth + 1)}"
            for sub in inner
        ]
        return " | ".join(parts)

    msg = str(exc) or type(exc).__name__
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        inner_msg = _unwrap_exception_detail(cause, depth + 1)
        return f"{msg} <- caused by {type(cause).__name__}: {inner_msg}"
    return msg
