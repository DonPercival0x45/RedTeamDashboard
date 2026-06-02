from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.api_keys import router as api_keys_router
from app.api.approvals import router as approvals_router
from app.api.authorizations import router as authorizations_router
from app.api.deps import AsyncRedisClient, DbSession
from app.api.engagements import router as engagements_router
from app.api.events import router as events_router
from app.api.reports import router as reports_router
from app.core.config import settings
from app.core.logging import configure_logging

configure_logging(settings.env)

app = FastAPI(title="Red Team Dashboard API", version="0.0.1")

# Phase 0: open CORS to the dev frontend origin so XHR + SSE work from the
# browser. Production behind a single domain won't need this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Last-Event-ID"],
)

app.include_router(engagements_router)
app.include_router(approvals_router)
app.include_router(authorizations_router)
app.include_router(api_keys_router)
app.include_router(events_router)
app.include_router(reports_router)


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
