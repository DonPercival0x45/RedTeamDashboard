"""DB-backed session-authorization lookup for the worker's graph.

``make_db_authorizer`` returns the ``Authorizer`` callable the dispatch node
uses to decide whether an active tool call is covered by a standing session
grant. It queries live on every call (not cached in run state) so a grant
created mid-run — e.g. by approving an interrupt with "remember for session" —
takes effect immediately for the rest of that run and all future runs.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Authorization
from app.worker.runner import SessionFactory


def make_db_authorizer(session_factory: SessionFactory):
    def authorizer(engagement_id: uuid.UUID | None, tool_name: str) -> uuid.UUID | None:
        if engagement_id is None:
            return None
        session: Session = session_factory()
        try:
            return session.execute(
                select(Authorization.id).where(
                    Authorization.engagement_id == engagement_id,
                    Authorization.tool_name == tool_name,
                    Authorization.revoked_at.is_(None),
                )
            ).scalar_one_or_none()
        finally:
            session.close()

    return authorizer
