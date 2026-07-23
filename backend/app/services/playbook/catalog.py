"""Playbook catalog helpers — seed loader + lookup.

Tiny surface on purpose: no CRUD API in A3a (the analyst-facing catalog UX
lands with A5 or a follow-up). The seed loader exists so tests + local dev
can populate a couple of well-known playbooks; the lookup gives the runner
+ higher-level callers a way to resolve ``(slug, version)`` to a row.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.playbook_seeds import SEED_PLAYBOOKS
from app.models import Playbook, PlaybookStep

logger = structlog.get_logger(__name__)


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
