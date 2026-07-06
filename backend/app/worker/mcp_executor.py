"""Worker → MCP tool execution.

Stage 1.5 of MCP composition: when the run envelope carries ``mcp_url`` +
``lease_token``, the dispatch node sends every tool invocation through the
MCP server over SSE instead of calling the local ``run_tool``. The MCP
server runs the actual tool, enforces scope a second time, and writes
``mcp.tool.X`` audit. The worker keeps owning finding persistence + event
emission so we don't fork the Postgres/Redis writers across two services.

This module exposes one factory: ``make_mcp_executor(mcp_url, lease_token,
*, api_key)``. It returns a synchronous callable matching the dispatch
node's ``run_tool`` shape — ``(tool_name, args) -> ToolResult`` — so the
graph code doesn't need an async-aware branch.

Why sync: the LangGraph dispatch node and the worker run on a sync thread
out of the Redis Streams consumer. Wrapping each call in a fresh asyncio
loop is correct (one tool call at a time per run) and avoids the cost of
making the entire worker loop async.

Why ``langchain-mcp-adapters``: it speaks the MCP wire protocol and
returns LangChain ``BaseTool`` objects, which gives us a stable invocation
surface. We don't use the tools for LLM binding here — schema binding
stays local-registry-filtered for Stage 1.5 — but we use them as a
typed HTTP client.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from typing import Any

import structlog

from app.orchestrator.tools.runtime import ToolResult

logger = structlog.get_logger(__name__)


MCPExecutor = Callable[[str, Mapping[str, Any]], ToolResult]


def make_mcp_executor(
    mcp_url: str,
    lease_token: str,
    *,
    api_key: str,
) -> MCPExecutor:
    """Build a sync ``(name, args) -> ToolResult`` callable that runs tools
    against ``mcp_url`` over SSE with the lease token attached.

    The returned executor lazily resolves the MCP tool list on first call
    and caches the name → ``BaseTool`` map so subsequent calls only pay
    one round trip. Each invocation runs in a fresh asyncio loop because
    the surrounding worker is sync — graph state and Redis I/O block.

    On any transport error or non-JSON response, returns ``ToolResult(ok=False,
    error=...)`` so the dispatch node's existing error path (write a denial,
    surface to the model) kicks in just like a local tool failure.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    headers = {"X-API-Key": api_key, "X-Lease-Token": lease_token}
    client = MultiServerMCPClient(
        {
            "rtd": {
                "url": mcp_url,
                "transport": "sse",
                "headers": headers,
            }
        }
    )

    tool_cache: dict[str, Any] = {}

    async def _load_tools() -> None:
        if tool_cache:
            return
        tools = await client.get_tools()
        for tool in tools:
            tool_cache[tool.name] = tool

    async def _ainvoke(name: str, args: Mapping[str, Any]) -> Any:
        await _load_tools()
        tool = tool_cache.get(name)
        if tool is None:
            raise KeyError(f"MCP server does not expose tool {name!r}")
        return await tool.ainvoke(dict(args))

    def _run(name: str, args: Mapping[str, Any]) -> ToolResult:
        try:
            raw = asyncio.run(_ainvoke(name, args))
        except KeyError as exc:
            return ToolResult(ok=False, error=str(exc))
        except BaseException as exc:  # noqa: BLE001 — surface MCP/transport errors as tool errors
            # v1.4.4: Python's BaseExceptionGroup (raised by asyncio.TaskGroup
            # on any child failure) stringifies to just "unhandled errors in
            # a TaskGroup (1 sub-exception)" — the real cause is inside. Walk
            # into it so the analyst sees WHAT actually broke (SSL failure,
            # 401 from MCP auth, DNS, etc.) instead of the wrapper.
            detail = _unwrap_exception_detail(exc)
            logger.exception(
                "worker.mcp_executor_failed",
                tool=name,
                error=detail,
                error_type=type(exc).__name__,
            )
            return ToolResult(
                ok=False,
                error=f"mcp transport error ({type(exc).__name__}): {detail}",
            )

        return _coerce_tool_response(raw)

    return _run


def _unwrap_exception_detail(exc: BaseException, depth: int = 0) -> str:
    """Return the deepest useful message from a possibly-nested exception,
    with a leading ``ClassName: `` on inner layers so the analyst can see
    what actually broke (SSL, connection refused, HTTP 401, etc.).

    Handles Python 3.11+ ``BaseExceptionGroup`` (asyncio.TaskGroup raises
    these on any child failure — their default ``str()`` is uselessly
    generic) plus the older ``__cause__`` / ``__context__`` chains.
    """
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


def _coerce_tool_response(raw: Any) -> ToolResult:
    """Normalize an MCP tool response into the worker's ``ToolResult`` shape.

    MCP tools return JSON-able dicts. Per the server convention:
      - ``{"error": "..."}`` → ``ToolResult(ok=False, error=...)``
      - ``{"findings": [...], "data": {...}}`` → leased response with raw
        findings handed back to the worker for persistence + emit
      - otherwise → ``ToolResult(ok=True, data=raw)``

    Strings come back as JSON-string content frames from some adapter
    versions; we tolerate that by trying a json.loads pass.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return ToolResult(ok=True, data={"raw": raw})

    if not isinstance(raw, Mapping):
        return ToolResult(ok=True, data={"value": raw})

    if "error" in raw:
        return ToolResult(ok=False, error=str(raw.get("error")))

    findings = raw.get("_lease_findings")
    if findings is not None and isinstance(findings, list):
        data = {k: v for k, v in raw.items() if k != "_lease_findings"}
        return ToolResult(ok=True, data=data, findings=list(findings))

    return ToolResult(ok=True, data=dict(raw))
