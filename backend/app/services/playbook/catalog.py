"""Playbook catalog helpers — seed loader + lookup + CRUD.

Analyst-facing CRUD lives here alongside the seed loader (A5b). Edits mutate
the latest version in place — no draft/publish machinery, no immutable
version history. A follow-up can add versioning if governance calls for it;
for now, ``version`` stays at 1 for analyst-authored playbooks and the seed
version-bump path handles the rare "publish v2 of a seeded playbook" case.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.playbook_seeds import SEED_PLAYBOOKS
from app.models import Playbook, PlaybookRun, PlaybookStep

logger = structlog.get_logger(__name__)


class PlaybookSlugConflictError(Exception):
    """Raised when a create tries to reuse an existing (slug, version)."""


class PlaybookHasRunsError(Exception):
    """Raised when a delete would orphan run rows (FK RESTRICT would kick,
    but we surface a friendly 409 before Postgres does)."""


class StepNotFoundError(Exception):
    """Raised when a step id doesn't belong to the target playbook."""


def load_seed_playbooks(session: Session) -> list[Playbook]:
    """Idempotent upsert of the seed playbooks.

    Per ``(slug, version)`` — existing rows are left alone; version bumps in
    the seed dict install side-by-side. Node/tool changes on an already-
    installed version are ignored; publish a new version instead.
    """
    installed: list[Playbook] = []
    for seed in SEED_PLAYBOOKS:
        existing = session.execute(
            select(Playbook).where(
                Playbook.slug == seed["slug"],
                Playbook.version == seed["version"],
            )
        ).scalar_one_or_none()
        if existing is not None:
            installed.append(existing)
            continue
        playbook = Playbook(
            slug=seed["slug"],
            version=seed["version"],
            name=seed["name"],
            description=seed.get("description"),
            applies_to_asset_class=seed["applies_to_asset_class"],
            active=seed.get("active", False),
        )
        session.add(playbook)
        session.flush()
        for step in seed["steps"]:
            session.add(
                PlaybookStep(
                    playbook_id=playbook.id,
                    sort_order=step.get("sort_order", 0),
                    tool_slug=step["tool_slug"],
                    args_template=step.get("args_template", {}),
                    satisfies_node_ids=step.get("satisfies_node_ids", []),
                    description=step.get("description"),
                )
            )
        session.flush()
        installed.append(playbook)
        logger.info(
            "playbook.seed_installed",
            slug=playbook.slug,
            version=playbook.version,
            step_count=len(seed["steps"]),
        )
    return installed


def get_by_slug(
    session: Session, slug: str, version: int | None = None
) -> Playbook | None:
    """Look up a playbook. ``version`` omitted → latest for that slug."""
    stmt = select(Playbook).where(Playbook.slug == slug)
    if version is not None:
        stmt = stmt.where(Playbook.version == version)
    stmt = stmt.order_by(Playbook.version.desc()).limit(1)
    return session.execute(stmt).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Playbook CRUD — A5b
# ---------------------------------------------------------------------------


def create_playbook(
    session: Session,
    *,
    slug: str,
    name: str,
    applies_to_asset_class: str,
    description: str | None = None,
    active: bool = False,
    version: int = 1,
) -> Playbook:
    """Create a new playbook at version 1 by default.

    Uniqueness on ``(slug, version)`` — the DB constraint plus this pre-check
    together turn "already exists" into a friendly error instead of a raw
    IntegrityError. Analyst-authored playbooks start at version 1; the seed
    loader handles side-by-side versioning for shipped catalog entries.
    """
    existing = get_by_slug(session, slug, version)
    if existing is not None:
        raise PlaybookSlugConflictError(
            f"playbook '{slug}' version {version} already exists"
        )
    playbook = Playbook(
        slug=slug,
        version=version,
        name=name,
        description=description,
        applies_to_asset_class=applies_to_asset_class,
        active=active,
    )
    session.add(playbook)
    try:
        session.flush()
    except IntegrityError as exc:  # noqa: BLE001 - race between the pre-check and INSERT
        raise PlaybookSlugConflictError(
            f"playbook '{slug}' version {version} already exists"
        ) from exc
    return playbook


def update_playbook(
    session: Session,
    *,
    playbook: Playbook,
    name: str | None = None,
    description: str | None = None,
    active: bool | None = None,
    applies_to_asset_class: str | None = None,
) -> Playbook:
    """Patch playbook metadata in place. ``None`` values leave the field
    unchanged so partial updates work cleanly."""
    if name is not None:
        playbook.name = name
    if description is not None:
        playbook.description = description
    if active is not None:
        playbook.active = active
    if applies_to_asset_class is not None:
        playbook.applies_to_asset_class = applies_to_asset_class
    session.flush()
    return playbook


def delete_playbook(session: Session, *, playbook: Playbook) -> None:
    """Delete a playbook + its steps. Refuses when runs reference it —
    the ``playbook_runs.playbook_id`` FK is RESTRICT so Postgres would
    reject the delete anyway; we surface a friendly error first."""
    run_count = session.execute(
        select(func.count()).select_from(PlaybookRun).where(
            PlaybookRun.playbook_id == playbook.id
        )
    ).scalar_one()
    if run_count:
        raise PlaybookHasRunsError(
            f"playbook '{playbook.slug}' has {run_count} runs; delete blocked"
        )
    session.delete(playbook)
    session.flush()


# ---------------------------------------------------------------------------
# Step CRUD
# ---------------------------------------------------------------------------


def _get_step(
    session: Session, playbook: Playbook, step_id: uuid.UUID
) -> PlaybookStep:
    step = session.get(PlaybookStep, step_id)
    if step is None or step.playbook_id != playbook.id:
        raise StepNotFoundError(
            f"step {step_id} not found on playbook '{playbook.slug}'"
        )
    return step


def add_step(
    session: Session,
    *,
    playbook: Playbook,
    tool_slug: str,
    args_template: dict[str, Any] | None = None,
    satisfies_node_ids: list[str] | None = None,
    sort_order: int | None = None,
    description: str | None = None,
) -> PlaybookStep:
    """Append a step. ``sort_order`` omitted → placed after the current
    highest so the runner keeps a stable order without the caller having
    to know."""
    if sort_order is None:
        max_order = session.execute(
            select(func.coalesce(func.max(PlaybookStep.sort_order), 0)).where(
                PlaybookStep.playbook_id == playbook.id
            )
        ).scalar_one()
        sort_order = int(max_order) + 10
    step = PlaybookStep(
        playbook_id=playbook.id,
        sort_order=sort_order,
        tool_slug=tool_slug,
        args_template=args_template or {},
        satisfies_node_ids=satisfies_node_ids or [],
        description=description,
    )
    session.add(step)
    session.flush()
    return step


def update_step(
    session: Session,
    *,
    playbook: Playbook,
    step_id: uuid.UUID,
    tool_slug: str | None = None,
    args_template: dict[str, Any] | None = None,
    satisfies_node_ids: list[str] | None = None,
    sort_order: int | None = None,
    description: str | None = None,
) -> PlaybookStep:
    """Patch a step in place. ``None`` values leave fields unchanged."""
    step = _get_step(session, playbook, step_id)
    if tool_slug is not None:
        step.tool_slug = tool_slug
    if args_template is not None:
        step.args_template = args_template
    if satisfies_node_ids is not None:
        step.satisfies_node_ids = satisfies_node_ids
    if sort_order is not None:
        step.sort_order = sort_order
    if description is not None:
        step.description = description
    session.flush()
    return step


def delete_step(
    session: Session,
    *,
    playbook: Playbook,
    step_id: uuid.UUID,
) -> None:
    step = _get_step(session, playbook, step_id)
    session.delete(step)
    session.flush()
