"""
Project X-Ray — Project Management and Governance Platform

This FastAPI application provides the HTTP and MCP (Model Context Protocol) surface
for managing authorized project work with approval-gated AI execution.

**Charter:**
- Approval-gated execution: Every active tool call passes a scope + risk gate and
  is recorded as an Approval with an immutable audit_log.
- Agents assist, analysts decide: Automated agents perform enumeration and analysis
  only. Validation/proof-of-concept work is analyst-only.
- In-scope enforcement: AI tooling runs only against targets explicitly
  defined by the analyst as in-scope.

The MCP server exposes tools for Claude Code and other AI assistants, with the same
approval gates and audit logging as the web UI.
"""
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import AsyncRedisClient, DbSession
from app.api.events import router as events_router
from app.api.reports import router as reports_router
from app.auth.routes import (
    api_keys_router,
    approvals_router,
    authorizations_router,
    provider_keys_router,
)
from app.findings.routes import router as findings_router
from app.observations.routes import router as observations_router
from app.projects.routes import router as projects_router
from app.runs.routes import router as runs_router
from app.scope.routes import router as scope_router
from app.tasks.routes import router as tasks_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.mcp.auth import MCPAuthMiddleware
from app.mcp.server import mcp

configure_logging(settings.env)

app = FastAPI(title="Project X-Ray API", version="0.0.1")

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

# Domain routers (vertical-slice refactor)
app.include_router(projects_router)
app.include_router(scope_router)
app.include_router(findings_router)
app.include_router(observations_router)
app.include_router(runs_router)
app.include_router(tasks_router)

# Auth domain (api_keys, approvals, authorizations, provider_keys)
app.include_router(api_keys_router)
app.include_router(approvals_router)
app.include_router(authorizations_router)
app.include_router(provider_keys_router)

# Remaining api/ routers (small, not yet split into domain packages)
app.include_router(events_router)
app.include_router(reports_router)

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
        status_code=(
            status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        content={
            "status": "ok" if healthy else "degraded",
            "env": settings.env,
            "db": db_ok,
            "redis": redis_ok,
        },
    )
