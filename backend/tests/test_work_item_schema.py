from datetime import UTC, datetime
from uuid import uuid4

from app.schemas.strategy import WorkItemRead


def test_work_item_read_projects_execution_anchors() -> None:
    now = datetime.now(UTC)
    scope_item_id = uuid4()
    entity_id = uuid4()

    projected = WorkItemRead.model_validate(
        {
            "id": uuid4(),
            "engagement_id": uuid4(),
            "objective_id": None,
            "parent_work_item_id": None,
            "scope_item_id": scope_item_id,
            "entity_id": entity_id,
            "title": "Scope-anchored agent work",
            "description": None,
            "rationale": None,
            "acceptance_criteria": [],
            "status": "ready",
            "priority": "medium",
            "executor_type": "finding_agent",
            "assigned_user_id": None,
            "created_by_user_id": None,
            "created_by_execution_id": uuid4(),
            "started_at": None,
            "blocked_reason": None,
            "due_at": None,
            "resolution_outcome": None,
            "resolution_note": None,
            "completed_by_user_id": None,
            "completed_at": None,
            "row_version": 1,
            "created_at": now,
            "updated_at": now,
            "finding_links": [],
        }
    )

    payload = projected.model_dump(mode="json")
    assert payload["scope_item_id"] == str(scope_item_id)
    assert payload["entity_id"] == str(entity_id)
