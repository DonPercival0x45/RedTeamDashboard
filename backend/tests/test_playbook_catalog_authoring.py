from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models import (
    AuditLog,
    Engagement,
    EngagementArchitecture,
    EngagementStatus,
    EngagementWorkState,
    Playbook,
    PlaybookRun,
    PlaybookStep,
    PlaybookStepExecution,
    ScopeItem,
    ScopeKind,
    User,
    UserRole,
)
from app.services.playbook import StepResult, execute_pending_run, load_seed_playbooks
from app.services.playbook.catalog import create_new_version


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_step(self, *, tool_slug, args_template, scope_context):
        self.calls.append(f"{tool_slug}:{scope_context}")
        return StepResult(ok=True, data={"items": []})


def _user(db: Session) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"catalog-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Catalog Author",
        role=UserRole.user,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def _headers(user: User) -> dict[str, str]:
    return {"X-User-Id": user.email}


def test_atomic_authoring_derives_policy_and_credentials(db: Session) -> None:
    client = TestClient(app)
    user = _user(db)
    slug = f"ip-custom-{uuid.uuid4().hex[:8]}"

    response = client.post(
        "/playbooks",
        headers=_headers(user),
        json={
            "slug": slug,
            "name": "IP ownership review",
            "description": "Created entirely in the catalog.",
            "category": "discovery",
            "applicable_entity_types": ["ip"],
            "active": False,
            "steps": [
                {
                    "tool_slug": "ipinfo",
                    "args_template": {"ip": "attacker-controlled.example"},
                    "satisfies_node_ids": ["forged.coverage"],
                    "description": "Collect ASN ownership",
                }
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["category"] == "discovery"
    assert body["applicable_entity_types"] == ["ip"]
    assert body["origin"] == "custom"
    assert body["required_credentials"] == ["ipinfo"]
    assert body["steps"][0]["id"]
    assert body["steps"][0]["args_template"] == {"ip": "{{scope_item}}"}
    assert body["steps"][0]["satisfies_node_ids"] == []
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.event_type == "playbook.created",
            AuditLog.actor_id == str(user.id),
        )
    )
    assert audit is not None
    assert audit.payload["steps"] == [
        {
            "id": body["steps"][0]["id"],
            "sort_order": 10,
            "tool_slug": "ipinfo",
            "transport": "mcp",
            "risk": "passive",
            "credential": "ipinfo",
        }
    ]


def test_active_tool_forces_approval_and_validates_entity_family(db: Session) -> None:
    client = TestClient(app)
    user = _user(db)
    response = client.post(
        "/playbooks",
        headers=_headers(user),
        json={
            "slug": f"active-custom-{uuid.uuid4().hex[:8]}",
            "name": "Bounded service validation",
            "category": "validation",
            "applicable_entity_types": ["domain", "subdomain", "host"],
            "active": False,
            "steps": [{"tool_slug": "port_scan"}],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["active"] is True
    args = response.json()["steps"][0]["args_template"]
    assert args["target"] == "{{scope_item}}"
    assert args["ports"] == "21,22,25,53,80,110,143,443,445,3389,5432,6379,8080,8443"
    assert args["__on_error"] == "stop"


def test_editing_system_playbook_publishes_new_version(db: Session) -> None:
    client = TestClient(app)
    user = _user(db)
    slug = f"system-{uuid.uuid4().hex[:8]}"
    previous = Playbook(
        slug=slug,
        version=1,
        name="System recipe",
        description="Shipped recipe",
        applies_to_asset_class="domain",
        applicable_entity_types=["domain"],
        category="discovery",
        origin="system",
        active=False,
    )
    db.add(previous)
    db.flush()
    source_step = PlaybookStep(
        playbook_id=previous.id,
        sort_order=10,
        tool_slug="whois",
        args_template={"domain": "{{scope_item}}", "trusted_option": [1, 2]},
        satisfies_node_ids=["osint.domain.whois"],
    )
    db.add(source_step)
    locked = Playbook(
        slug=f"locked-{uuid.uuid4().hex[:8]}",
        version=1,
        name="Locked system recipe",
        applies_to_asset_class="domain",
        applicable_entity_types=["domain"],
        category="discovery",
        origin="system",
        active=False,
    )
    db.add(locked)
    db.commit()

    response = client.post(
        f"/playbooks/{slug}/versions",
        headers=_headers(user),
        json={
            "expected_supersedes_id": str(previous.id),
            "expected_version": previous.version,
            "name": "Focused passive domain recon",
            "description": "Operator-curated edition.",
            "category": "discovery",
            "applicable_entity_types": ["domain", "subdomain", "host"],
            "active": False,
            "steps": [
                {
                    "tool_slug": "whois",
                    "source_step_id": str(source_step.id),
                    "description": "Registration",
                },
                {"tool_slug": "dns-inventory", "description": "DNS"},
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"] == previous.version + 1
    assert body["origin"] == "custom"
    assert body["supersedes_id"] == str(previous.id)
    assert body["steps"][0]["args_template"] == {
        "domain": "{{scope_item}}",
        "trusted_option": [1, 2],
    }
    assert body["steps"][0]["satisfies_node_ids"] == ["osint.domain.whois"]
    assert db.get(Playbook, previous.id) is not None

    stale = client.post(
        f"/playbooks/{slug}/versions",
        headers=_headers(user),
        json={
            "expected_supersedes_id": str(previous.id),
            "expected_version": previous.version,
            "name": "Stale replacement",
            "category": "discovery",
            "applicable_entity_types": ["domain"],
            "active": False,
            "steps": [{"tool_slug": "whois"}],
        },
    )
    assert stale.status_code == 409
    assert "reload" in stale.text.lower()

    delete_response = client.delete(f"/playbooks/{locked.slug}", headers=_headers(user))
    assert delete_response.status_code == 409


def test_every_shipped_recipe_round_trips_through_immutable_editing(db: Session) -> None:
    user = _user(db)
    load_seed_playbooks(db)
    db.flush()
    latest_by_slug: dict[str, Playbook] = {}
    for source in db.scalars(
        select(Playbook).where(Playbook.origin == "system").order_by(Playbook.version)
    ):
        latest_by_slug[source.slug] = source

    for seed in latest_by_slug.values():
        if not seed.steps:
            continue
        source = Playbook(
            slug=f"roundtrip-{uuid.uuid4().hex[:12]}",
            version=1,
            name=seed.name,
            description=seed.description,
            applies_to_asset_class=seed.applies_to_asset_class,
            applicable_entity_types=list(seed.applicable_entity_types),
            category=seed.category,
            origin="system",
            active=seed.active,
        )
        db.add(source)
        db.flush()
        for seed_step in seed.steps:
            db.add(
                PlaybookStep(
                    playbook_id=source.id,
                    sort_order=seed_step.sort_order,
                    tool_slug=seed_step.tool_slug,
                    args_template=seed_step.args_template,
                    satisfies_node_ids=seed_step.satisfies_node_ids,
                    description=seed_step.description,
                )
            )
        db.flush()
        db.refresh(source)
        expected = [
            (
                step.tool_slug,
                step.args_template,
                step.satisfies_node_ids,
            )
            for step in source.steps
        ]
        published = create_new_version(
            db,
            slug=source.slug,
            expected_supersedes_id=source.id,
            expected_version=source.version,
            name=source.name,
            description=source.description,
            category=source.category,
            applicable_entity_types=list(source.applicable_entity_types),
            active=source.active,
            steps=[
                {
                    "tool_slug": step.tool_slug,
                    "source_step_id": step.id,
                    "description": step.description,
                }
                for step in source.steps
            ],
            created_by=user.id,
        )
        assert [
            (step.tool_slug, step.args_template, step.satisfies_node_ids)
            for step in published.steps
        ] == expected


def test_catalog_options_are_server_owned(db: Session) -> None:
    client = TestClient(app)
    user = _user(db)
    response = client.get("/playbook-catalog/options", headers=_headers(user))
    assert response.status_code == 200
    body = response.json()
    assert "discovery" in body["categories"]
    assert "subdomain" in body["entity_types"]
    port_scan = next(tool for tool in body["tools"] if tool["slug"] == "port_scan")
    assert port_scan["risk"] == "active"
    assert "domain" in port_scan["target_kinds"]


def test_scope_is_rechecked_immediately_before_dispatch(db: Session) -> None:
    client = TestClient(app)
    user = _user(db)
    engagement = Engagement(
        name="Fresh scope gate",
        slug=f"scope-gate-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
        intelligence_architecture=EngagementArchitecture.v3,
    )
    db.add(engagement)
    db.flush()
    db.add(
        ScopeItem(
            engagement_id=engagement.id,
            kind=ScopeKind.domain,
            value="example.com",
            is_exclusion=False,
        )
    )
    db.commit()

    playbook_response = client.post(
        "/playbooks",
        headers=_headers(user),
        json={
            "slug": f"fresh-gate-{uuid.uuid4().hex[:8]}",
            "name": "Fresh gate",
            "category": "discovery",
            "applicable_entity_types": ["domain"],
            "steps": [{"tool_slug": "whois"}],
        },
    )
    playbook = playbook_response.json()
    payload = {
        "playbook_slug": playbook["slug"],
        "playbook_version": playbook["version"],
        "scope_subset": ["example.com"],
        "executor": playbook["required_executor"],
    }
    plan = client.post(
        f"/engagements/{engagement.slug}/playbook-runs/plan",
        headers=_headers(user),
        json=payload,
    ).json()
    payload["plan_sha256"] = plan["plan_sha256"]
    run_response = client.post(
        f"/engagements/{engagement.slug}/playbook-runs",
        headers=_headers(user),
        json=payload,
    )
    assert run_response.status_code == 202, run_response.text
    run_id = uuid.UUID(run_response.json()["id"])

    db.add(
        ScopeItem(
            engagement_id=engagement.id,
            kind=ScopeKind.domain,
            value="example.com",
            is_exclusion=True,
        )
    )
    db.commit()

    executor = RecordingExecutor()
    result = execute_pending_run(db, run_id=run_id, executor=executor)
    db.commit()
    assert executor.calls == []
    assert result.status.value == "failed"
    receipt = db.scalar(
        select(PlaybookStepExecution).where(PlaybookStepExecution.playbook_run_id == run_id)
    )
    assert receipt is not None
    assert "no longer authorized" in (receipt.error or "")
    assert db.get(PlaybookRun, run_id) is not None
