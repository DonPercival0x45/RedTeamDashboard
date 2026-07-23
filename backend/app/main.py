"""
RedTeamDashboard — Defensive Security Operations and Governance Platform

This FastAPI application provides the HTTP and MCP (Model Context Protocol) surface
for managing authorized security engagements.

**Charter:**
- Approval-gated execution: Every active tool call passes a scope + risk gate and
  is recorded as an Approval with an immutable audit_log.
- Agents assist, analysts decide: Automated agents perform enumeration and scanning
  only. Validation/proof-of-concept work is analyst-only.
- In-scope enforcement: Recon/OSINT tooling runs only against targets explicitly
  defined by the analyst as in-scope.

The MCP server exposes tools for Claude Code and other AI assistants, with the same
approval gates and audit logging as the web UI.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.admin_users import router as admin_users_router
from app.api.agent_configurations import router as agent_configurations_router
from app.api.analytics import router as analytics_router
from app.api.api_keys import router as api_keys_router
from app.api.approvals import router as approvals_router
from app.api.authorizations import router as authorizations_router
from app.api.completion import router as completion_router
from app.api.contributions import router as contributions_router
from app.api.deps import AsyncRedisClient, DbSession
from app.api.engagement_strategist import router as engagement_strategist_router
from app.api.engagements import router as engagements_router
from app.api.entities import router as entities_router
from app.api.events import router as events_router
from app.api.infrastructure import router as infrastructure_router
from app.api.integrations import router as integrations_router
from app.api.me import router as me_router
from app.api.methodology import router as methodology_router
from app.api.orchestrator import router as orchestrator_router
from app.api.orchestrator_tools import router as orchestrator_tools_router
from app.api.provider_keys import router as provider_keys_router
from app.api.releases import router as releases_router
from app.api.reports import router as reports_router
from app.api.roadmap_suggestions import router as roadmap_suggestions_router
from app.api.status import router as status_router
from app.api.strategy import router as strategy_router
from app.api.strategy_suggestions import router as strategy_suggestions_router
from app.api.tool_invocations import router as tool_invocations_router
from app.api.tools import router as tools_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.mcp.auth import MCPAuthMiddleware
from app.mcp.server import mcp

configure_logging(settings.env)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks for the API process. Currently a no-op —
    the workflow-templates seed was removed in v0.5.0 (feature dropped)."""
    yield


app = FastAPI(title="Red Team Dashboard API", version="0.0.1", lifespan=lifespan)

# CORS for the browser viewer. Defaults cover local dev; Phase 6 central
# viewer adds its origin via the CORS_ALLOW_ORIGINS env var (Bicep param).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Last-Event-ID"],
)

app.include_router(analytics_router)
app.include_router(engagements_router)
app.include_router(approvals_router)
app.include_router(authorizations_router)
app.include_router(api_keys_router)
app.include_router(events_router)
app.include_router(orchestrator_router)
app.include_router(orchestrator_tools_router)
app.include_router(provider_keys_router)
app.include_router(reports_router)
app.include_router(entities_router)
app.include_router(me_router)
app.include_router(methodology_router)
app.include_router(roadmap_suggestions_router)
app.include_router(integrations_router)
app.include_router(infrastructure_router)
app.include_router(admin_users_router)
app.include_router(status_router)
app.include_router(strategy_router)
app.include_router(completion_router)
app.include_router(engagement_strategist_router)
app.include_router(strategy_suggestions_router)
app.include_router(contributions_router)
app.include_router(tools_router)
app.include_router(releases_router)
app.include_router(tool_invocations_router)
app.include_router(agent_configurations_router)

# MCP server — auth-gated SSE endpoint for agent clients (Claude Code, etc.)
# Agents connect via: claude mcp add rtd --transport sse --url https://<fqdn>/mcp/sse
app.mount("/mcp", MCPAuthMiddleware(mcp.sse_app()))


@app.get("/health")
async def health(session: DbSession, redis: AsyncRedisClient) -> JSONResponse:
    """Liveness + dependency readiness probe.

    Returns 200 only if Postgres + Redis both respond. Compose's healthcheck
    polls this, so an unhealthy DB or Redis correctly bubbles up as the
    backend container going unhealthy rather than just "uvicorn is listening".
    """
    db_ok = True
    redis_ok = True
    try:
        session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — any failure means not ready
        db_ok = False
    try:
        await redis.ping()
    except Exception:  # noqa: BLE001
        redis_ok = False

    healthy = db_ok and redis_ok
    return JSONResponse(
        status_code=(status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE),
        content={
            "status": "ok" if healthy else "degraded",
            "env": settings.env,
            "db": db_ok,
            "redis": redis_ok,
        },
    )
