from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.models import WorkerInstance, WorkerOperationalEvent
from app.services.worker_observability import WorkerRuntime
from app.worker.healthcheck import main as healthcheck_main


def test_worker_runtime_persists_heartbeats_components_and_redacted_events(
    monkeypatch,
    tmp_path,
) -> None:
    health_file = tmp_path / "worker-id"
    runtime = WorkerRuntime(
        session_factory=SessionLocal,
        role="playbook",
        concurrency=2,
        health_file=str(health_file),
    )
    runtime.start()
    runtime.component_started(
        "playbook-lane",
        slot=0,
        owner_token=f"owner-{uuid.uuid4()}",
    )
    run_id = None
    runtime.component_heartbeat(
        "playbook-lane",
        slot=0,
        state="busy",
        current_run_id=run_id,
    )
    runtime.record_event(
        event_type="heartbeat.failed",
        message="authorization=Bearer definitely-secret",
        component="playbook-lane",
        slot=0,
        details={"api_key": "also-secret", "safe": "visible"},
    )

    monkeypatch.setenv("RTD_WORKER_ROLE", "playbook")
    monkeypatch.setenv("RTD_WORKER_HEALTH_FILE", str(health_file))
    assert healthcheck_main() == 0

    try:
        with SessionLocal() as session:
            instance = session.get(WorkerInstance, runtime.id)
            assert instance is not None
            assert instance.concurrency == 2
            assert instance.stopped_at is None
            event = session.execute(
                select(WorkerOperationalEvent).where(
                    WorkerOperationalEvent.worker_instance_id == runtime.id
                )
            ).scalar_one()
            assert "definitely-secret" not in event.message
            assert event.details["api_key"] == "[REDACTED]"
            assert event.details["safe"] == "visible"

        runtime.stop("test complete")
        assert healthcheck_main() == 1
    finally:
        with SessionLocal() as cleanup:
            cleanup.execute(
                delete(WorkerOperationalEvent).where(
                    WorkerOperationalEvent.worker_instance_id == runtime.id
                )
            )
            cleanup.execute(delete(WorkerInstance).where(WorkerInstance.id == runtime.id))
            cleanup.commit()
