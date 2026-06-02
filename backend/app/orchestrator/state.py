"""LangGraph state for the OSINT agent.

The state is what's checkpointed between nodes. Fields with reducers
(``Annotated[..., reducer]``) accumulate across node returns; non-annotated
fields overwrite. ``scope_items`` is set once at run start and read by the
dispatch node; we keep it in state so the checkpoint is self-contained.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict
from uuid import UUID

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from app.orchestrator.scope import ScopeSnapshot


class OsintState(TypedDict, total=False):
    engagement_id: UUID
    messages: Annotated[list[BaseMessage], add_messages]
    scope_items: list[ScopeSnapshot]
    findings: Annotated[list[dict[str, Any]], operator.add]
    denials: Annotated[list[dict[str, Any]], operator.add]
    pending: Annotated[list[dict[str, Any]], operator.add]
    # Active calls that auto-approved via a standing session authorization
    # instead of interrupting — recorded so the worker can audit-log them.
    auto_approvals: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]
