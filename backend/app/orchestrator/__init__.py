"""Orchestrator package: authorization spine + LangGraph runtime.

Public surface:
- ``ToolSpec`` / ``get_tool`` / ``all_tools`` — per-tool risk + target metadata.
- ``scope_check`` / ``approval_check`` / ``evaluate`` — the gate.
- ``OsintState`` / ``build_graph`` — the LangGraph runtime.
"""
from app.orchestrator.gate import (
    Action,
    Decision,
    ScopeDecision,
    approval_check,
    evaluate,
    scope_check,
)
from app.orchestrator.graph import build_graph
from app.orchestrator.state import OsintState
from app.orchestrator.tools import ToolSpec, all_tools, get_tool

__all__ = [
    "Action",
    "Decision",
    "OsintState",
    "ScopeDecision",
    "ToolSpec",
    "all_tools",
    "approval_check",
    "build_graph",
    "evaluate",
    "get_tool",
    "scope_check",
]
