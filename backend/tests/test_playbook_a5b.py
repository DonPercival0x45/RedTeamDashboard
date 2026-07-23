"""A5b tests — analyst catalog CRUD (edit-in-place).

Covers:

- Service layer: create/update/delete playbook, add/update/delete step.
- Slug conflict raises PlaybookSlugConflictError.
- Delete refuses when runs exist (PlaybookHasRunsError).
- Step CRUD is scoped to the parent playbook (step from another playbook
  raises StepNotFoundError).
- Auto sort_order on add_step places after current max +10.
- HTTP: POST/PATCH/DELETE for playbooks + steps. 201/200/204/404/409.
- Guest 403 on every write.
- End-to-end: create active playbook via API → enqueue against it →
  status=awaiting_approval (proves the A5 gate reads what CRUD wrote).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.main import app
from app.models import (
    Engagement,
    EngagementStatus,
    EngagementWorkState,
    Playbook,
    PlaybookRun,
    PlaybookRunStatus,
    PlaybookStep,
    User,
    UserRole,
)
from app.services import methodology as meth
from app.services.playbook import (
    PlaybookHasRunsError,
    PlaybookSlugConflictError,
    StepNotFoundError,
    add_step,
    catalog,
    create_playbook,
    delete_playbook,
    delete_step,
    enqueue_run,
    load_seed_playbooks,
    update_playbook,
    update_step,
)


@pytest.fixture(autouse=True)
def _cleanup_queue():
    s = SessionLocal()
    try:
        s.execute(delete(PlaybookRun))
        s.commit()
    finally:
        s.close()
    yield


@pytest.fixture()
def custom_playbook(db: Session) -> Playbook:
    """A5b-authored playbook the tests mutate. Distinct from seeds so we
    can freely delete it without breaking other tests."""
    return create_playbook(
        db,
        slug=f"a5b-{uuid.uuid4().hex[:6]}",
        name="A5b test playbook",
        applies_to_asset_class="domain",
        active=False,
    )


# ---------------------------------------------------------------------------
# create_playbook
# ---------------------------------------------------------------------------


def test_create_playbook_sets_version_1_and_defaults(db: Session) -> None:
    pb = create_playbook(
        db,
        slug=f"crt-{uuid.uuid4().hex[:6]}",
        name="Created via A5b",
        applies_to_asset_class="ip",
    )
    db.flush()
    assert pb.version == 1
    assert pb.active is False
    assert pb.steps == []


def test_create_playbook_duplicate_slug_raises(db: Session) -> None:
    slug = f"dup-{uuid.uuid4().hex[:6]}"
    create_playbook(db, slug=slug, name="First", applies_to_asset_class="domain")
    db.flush()
    with pytest.raises(PlaybookSlugConflictError):
        create_playbook(
            db, slug=slug, name="Second collision", applies_to_asset_class="ip",
        )


# ---------------------------------------------------------------------------
# update_playbook
# ---------------------------------------------------------------------------


def test_update_playbook_partial(db: Session, custom_playbook: Playbook) -> None:
    """Only fields present in the patch change; None leaves them alone."""
    original_asset_class = custom_playbook.applies_to_asset_class
    update_playbook(db, playbook=custom_playbook, name="renamed", active=True)
    db.flush()
    db.refresh(custom_playbook)
    assert custom_playbook.name == "renamed"
    assert custom_playbook.active is True
    assert custom_playbook.applies_to_asset_class == original_asset_class


# ---------------------------------------------------------------------------
# delete_playbook
# ---------------------------------------------------------------------------


def test_delete_playbook_removes_row(db: Session, custom_playbook: Playbook) -> None:
    slug = custom_playbook.slug
    delete_playbook(db, playbook=custom_playbook)
    db.flush()
    assert catalog.get_by_slug(db, slug) is None


def test_delete_playbook_refuses_when_runs_exist(
    db: Session, custom_playbook: Playbook
) -> None:
    """The FK is RESTRICT; the service pre-checks so callers get a friendly
    error rather than an IntegrityError leak."""
    eng = Engagement(
        name="A5b delete test",
        slug=f"a5bd-{uuid.uuid4().hex[:6]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    enqueue_run(
        db, engagement=eng, playbook=custom_playbook, scope_subset=["foo.com"],
    )
    db.commit()
    with pytest.raises(PlaybookHasRunsError):
        delete_playbook(db, playbook=custom_playbook)


# ---------------------------------------------------------------------------
# Step CRUD
# ---------------------------------------------------------------------------


def test_add_step_auto_sort_order_places_after_max(
    db: Session, custom_playbook: Playbook
) -> None:
    s1 = add_step(db, playbook=custom_playbook, tool_slug="whois")
    s2 = add_step(db, playbook=custom_playbook, tool_slug="dns-inventory")
    db.flush()
    # Auto-placed: s1 gets 0+10=10, s2 gets 10+10=20.
    assert s1.sort_order == 10
    assert s2.sort_order == 20


def test_add_step_respects_explicit_sort_order(
    db: Session, custom_playbook: Playbook
) -> None:
    s = add_step(
        db, playbook=custom_playbook, tool_slug="whois", sort_order=5,
    )
    db.flush()
    assert s.sort_order == 5


def test_update_step_partial(db: Session, custom_playbook: Playbook) -> None:
    step = add_step(
        db, playbook=custom_playbook, tool_slug="whois",
        args_template={"domain": "{{scope_item}}"},
    )
    db.flush()
    update_step(
        db, playbook=custom_playbook, step_id=step.id,
        description="edited",
        sort_order=99,
    )
    db.flush()
    db.refresh(step)
    assert step.description == "edited"
    assert step.sort_order == 99
    assert step.tool_slug == "whois"  # unchanged


def test_update_step_wrong_playbook_raises(db: Session) -> None:
    """A step id from playbook A must not be editable via playbook B."""
    pb_a = create_playbook(
        db, slug=f"a-{uuid.uuid4().hex[:6]}", name="A",
        applies_to_asset_class="domain",
    )
    pb_b = create_playbook(
        db, slug=f"b-{uuid.uuid4().hex[:6]}", name="B",
        applies_to_asset_class="domain",
    )
    step_on_a = add_step(db, playbook=pb_a, tool_slug="whois")
    db.flush()
    with pytest.raises(StepNotFoundError):
        update_step(db, playbook=pb_b, step_id=step_on_a.id, description="hi")


def test_delete_step_removes_row(db: Session, custom_playbook: Playbook) -> None:
    step = add_step(db, playbook=custom_playbook, tool_slug="whois")
    db.flush()
    step_id = step.id
    delete_step(db, playbook=custom_playbook, step_id=step_id)
    db.flush()
    assert db.get(PlaybookStep, step_id) is None


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def user(db: Session) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"a5b-{uuid.uuid4().hex[:6]}@example.com",
        display_name="A5b Tester",
        role=UserRole.user,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def guest_user(db: Session) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"a5b-guest-{uuid.uuid4().hex[:6]}@example.com",
        display_name="A5b Guest",
        role=UserRole.guest,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


def _h(u: User) -> dict[str, str]:
    return {"X-User-Id": u.email}


def test_post_playbooks_creates_playbook(
    db: Session, client: TestClient, user: User
) -> None:
    slug = f"api-{uuid.uuid4().hex[:6]}"
    resp = client.post(
        "/playbooks",
        headers=_h(user),
        json={
            "slug": slug,
            "name": "Via API",
            "applies_to_asset_class": "domain",
            "active": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == slug
    assert body["version"] == 1
    assert body["active"] is True
    # Round-trip through GET.
    detail = client.get(f"/playbooks/{slug}", headers=_h(user))
    assert detail.status_code == 200


def test_post_playbooks_slug_conflict_409(
    db: Session, client: TestClient, user: User
) -> None:
    slug = f"dup-{uuid.uuid4().hex[:6]}"
    client.post(
        "/playbooks", headers=_h(user),
        json={"slug": slug, "name": "one", "applies_to_asset_class": "domain"},
    )
    resp = client.post(
        "/playbooks", headers=_h(user),
        json={"slug": slug, "name": "two", "applies_to_asset_class": "ip"},
    )
    assert resp.status_code == 409


def test_post_playbooks_guest_blocked(
    db: Session, client: TestClient, guest_user: User
) -> None:
    resp = client.post(
        "/playbooks", headers=_h(guest_user),
        json={"slug": "x", "name": "x", "applies_to_asset_class": "domain"},
    )
    assert resp.status_code == 403


def test_patch_playbook_updates_metadata(
    db: Session, client: TestClient, user: User
) -> None:
    slug = f"pat-{uuid.uuid4().hex[:6]}"
    client.post(
        "/playbooks", headers=_h(user),
        json={"slug": slug, "name": "orig", "applies_to_asset_class": "domain"},
    )
    resp = client.patch(
        f"/playbooks/{slug}", headers=_h(user),
        json={"name": "renamed", "active": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "renamed"
    assert body["active"] is True


def test_patch_playbook_unknown_404(
    db: Session, client: TestClient, user: User
) -> None:
    resp = client.patch(
        "/playbooks/never", headers=_h(user), json={"name": "x"},
    )
    assert resp.status_code == 404


def test_delete_playbook_removes_it(
    db: Session, client: TestClient, user: User
) -> None:
    slug = f"del-{uuid.uuid4().hex[:6]}"
    client.post(
        "/playbooks", headers=_h(user),
        json={"slug": slug, "name": "todel", "applies_to_asset_class": "domain"},
    )
    resp = client.delete(f"/playbooks/{slug}", headers=_h(user))
    assert resp.status_code == 204
    # Confirm gone.
    detail = client.get(f"/playbooks/{slug}", headers=_h(user))
    assert detail.status_code == 404


def test_delete_playbook_with_runs_409(
    db: Session, client: TestClient, user: User, custom_playbook: Playbook
) -> None:
    eng = Engagement(
        name="A5b HTTP delete",
        slug=f"a5bh-{uuid.uuid4().hex[:6]}",
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    enqueue_run(
        db, engagement=eng, playbook=custom_playbook, scope_subset=["foo.com"],
    )
    db.commit()
    resp = client.delete(f"/playbooks/{custom_playbook.slug}", headers=_h(user))
    assert resp.status_code == 409


def test_post_step_endpoint_appends(
    db: Session, client: TestClient, user: User
) -> None:
    slug = f"stps-{uuid.uuid4().hex[:6]}"
    client.post(
        "/playbooks", headers=_h(user),
        json={"slug": slug, "name": "s", "applies_to_asset_class": "domain"},
    )
    resp = client.post(
        f"/playbooks/{slug}/steps", headers=_h(user),
        json={
            "tool_slug": "whois",
            "args_template": {"domain": "{{scope_item}}"},
            "description": "WHOIS",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tool_slug"] == "whois"
    assert body["sort_order"] == 10  # first step auto-placed at 10


def test_patch_step_endpoint_edits(
    db: Session, client: TestClient, user: User
) -> None:
    slug = f"pste-{uuid.uuid4().hex[:6]}"
    client.post(
        "/playbooks", headers=_h(user),
        json={"slug": slug, "name": "s", "applies_to_asset_class": "domain"},
    )
    client.post(
        f"/playbooks/{slug}/steps", headers=_h(user),
        json={"tool_slug": "whois"},
    )
    # PlaybookStepRead doesn't expose id; look it up via the DB.
    from sqlalchemy import select as _select

    from app.models import Playbook as _Playbook
    from app.models import PlaybookStep as _PlaybookStep

    with SessionLocal() as s:
        pb = s.execute(_select(_Playbook).where(_Playbook.slug == slug)).scalar_one()
        st = s.execute(
            _select(_PlaybookStep).where(_PlaybookStep.playbook_id == pb.id)
        ).scalar_one()
        step_id = str(st.id)

    resp = client.patch(
        f"/playbooks/{slug}/steps/{step_id}", headers=_h(user),
        json={"description": "edited"},
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "edited"


def test_delete_step_endpoint_removes(
    db: Session, client: TestClient, user: User
) -> None:
    slug = f"dste-{uuid.uuid4().hex[:6]}"
    client.post(
        "/playbooks", headers=_h(user),
        json={"slug": slug, "name": "s", "applies_to_asset_class": "domain"},
    )
    client.post(
        f"/playbooks/{slug}/steps", headers=_h(user),
        json={"tool_slug": "whois"},
    )
    from sqlalchemy import select as _select

    from app.models import Playbook as _Playbook
    from app.models import PlaybookStep as _PlaybookStep

    with SessionLocal() as s:
        pb = s.execute(_select(_Playbook).where(_Playbook.slug == slug)).scalar_one()
        st = s.execute(
            _select(_PlaybookStep).where(_PlaybookStep.playbook_id == pb.id)
        ).scalar_one()
        step_id = str(st.id)

    resp = client.delete(f"/playbooks/{slug}/steps/{step_id}", headers=_h(user))
    assert resp.status_code == 204
    detail = client.get(f"/playbooks/{slug}", headers=_h(user))
    assert detail.json()["steps"] == []


# ---------------------------------------------------------------------------
# End-to-end: analyst creates active playbook → gate fires
# ---------------------------------------------------------------------------


def test_analyst_authored_active_playbook_hits_the_gate(
    db: Session, client: TestClient, user: User
) -> None:
    """The whole A5b→A5 seam: an analyst creates a playbook via the CRUD
    API with ``active=True``, adds a step, then kicks a run. The run should
    enter ``awaiting_approval`` — the same gate that A5 landed."""
    load_seed_playbooks(db)  # for the engagement's methodology snapshot
    db.commit()

    slug = f"gated-{uuid.uuid4().hex[:6]}"
    client.post(
        "/playbooks", headers=_h(user),
        json={
            "slug": slug, "name": "Gated by analyst",
            "applies_to_asset_class": "domain", "active": True,
        },
    )
    client.post(
        f"/playbooks/{slug}/steps", headers=_h(user),
        json={"tool_slug": "whois", "args_template": {"domain": "{{scope_item}}"}},
    )

    # Create an engagement with a methodology snapshot.
    eng_slug = f"e2e-{uuid.uuid4().hex[:6]}"
    eng = Engagement(
        name="E2E gate",
        slug=eng_slug,
        status=EngagementStatus.active,
        work_state=EngagementWorkState.active,
    )
    db.add(eng)
    db.flush()
    meth.load_seed_catalog(db)
    meth.select_for_engagement(
        db, engagement_id=eng.id, slug="osint-minimal",
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    db.commit()

    resp = client.post(
        f"/engagements/{eng_slug}/playbook-runs", headers=_h(user),
        json={"playbook_slug": slug, "scope_subset": ["foo.com"]},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == PlaybookRunStatus.awaiting_approval.value
