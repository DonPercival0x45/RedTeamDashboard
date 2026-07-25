"""Docker exec healthcheck for durable worker process heartbeats."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import WorkerInstance


def main() -> int:
    role = os.getenv("RTD_WORKER_ROLE", "core")
    path = Path(os.getenv("RTD_WORKER_HEALTH_FILE", f"/tmp/rtd-{role}-worker-id"))
    try:
        worker_id = UUID(path.read_text(encoding="utf-8").strip())
        with SessionLocal() as session:
            row = session.get(WorkerInstance, worker_id)
            if row is None or row.stopped_at is not None:
                return 1
            cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.worker_stale_after)
            return 0 if row.heartbeat_at >= cutoff else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
