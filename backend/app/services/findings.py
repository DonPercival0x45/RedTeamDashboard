"""Canonical lookup helpers for active (not soft-deleted) findings."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Finding


def get_active_finding_or_404(session: Session, finding_id: uuid.UUID) -> Finding:
    """Return a visible finding or hide missing/soft-deleted rows behind 404."""
    finding = session.execute(
        select(Finding).where(
            Finding.id == finding_id,
            Finding.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return finding


def lock_active_finding_or_404(session: Session, finding_id: uuid.UUID) -> Finding:
    """Lock and return a visible finding, rechecking soft-delete state."""
    finding = session.execute(
        select(Finding)
        .where(
            Finding.id == finding_id,
            Finding.deleted_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return finding
