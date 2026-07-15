"""HTTP surface for Settings > Configurations (v1.24.0).

Per-analyst per-engagement pinning of which LLM model each agent role
(Strategic / Tactical / Correlate) uses. Fallback chain at run-time:
this row -> ``users.default_model`` -> agent hardcoded default. The
provider key is resolved from the analyst's ephemeral BYO cache
independently.

Endpoints::

    GET    /agent-configurations             -> list current user's configs
    PUT    /agent-configurations/{slug}      -> upsert three optional roles
    DELETE /agent-configurations/{slug}      -> clear all three for user+eng
    GET    /agent-configurations/export      -> JSON download
    POST   /agent-configurations/import      -> apply, return counts

Per-user isolation is enforced at the query layer: every row is filtered
by ``user_id = current_user.id``. Configs are never visible across
analysts.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentNonGuestUser, DbSession
from app.models import AgentModelPreference, AgentName, Engagement
from app.schemas.agent_config import (
    AgentConfigExport,
    AgentConfigImportResult,
    AgentConfigListResponse,
    AgentConfigPut,
    AgentConfigRead,
    AgentConfigRolePayload,
    is_configurable_role,
)

router = APIRouter()

# Roles this bundle exposes. Storage type is the wider AgentName enum.
_ROLES: tuple[AgentName, ...] = (
    AgentName.strategic,
    AgentName.engagement_strategist,
    AgentName.tactical,
    AgentName.correlate,
)


def _engagement_by_slug(session: Session, slug: str) -> Engagement:
    eng = session.execute(select(Engagement).where(Engagement.slug == slug)).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail=f"engagement '{slug}' not found")
    return eng


def _rows_for_user(session: Session, user_id) -> dict[tuple[str, str], AgentModelPreference]:
    """Map ``(engagement_slug, role) -> preference row`` for the user."""
    rows = session.execute(
        select(AgentModelPreference, Engagement.slug)
        .join(Engagement, Engagement.id == AgentModelPreference.engagement_id)
        .where(AgentModelPreference.user_id == user_id)
    ).all()
    out: dict[tuple[str, str], AgentModelPreference] = {}
    for pref, slug in rows:
        out[(slug, pref.agent_role.value)] = pref
    return out


def _grouped(
    rows: dict[tuple[str, str], AgentModelPreference],
) -> dict[str, dict[str, AgentModelPreference]]:
    """Regroup as ``slug -> {role: row}`` for read-shape assembly."""
    out: dict[str, dict[str, AgentModelPreference]] = {}
    for (slug, role), pref in rows.items():
        out.setdefault(slug, {})[role] = pref
    return out


def _to_read(slug: str, by_role: dict[str, AgentModelPreference]) -> AgentConfigRead:
    latest = max(
        (r.updated_at for r in by_role.values() if r.updated_at),
        default=None,
    )
    first = next(iter(by_role.values()))
    return AgentConfigRead(
        engagement_id=first.engagement_id,
        engagement_slug=slug,
        strategic=(by_role["strategic"].model if "strategic" in by_role else None),
        engagement_strategist=(
            by_role["engagement_strategist"].model if "engagement_strategist" in by_role else None
        ),
        tactical=(by_role["tactical"].model if "tactical" in by_role else None),
        correlate=(by_role["correlate"].model if "correlate" in by_role else None),
        updated_at=latest,
    )


def _upsert_one(
    session: Session,
    *,
    user_id,
    engagement_id,
    role: AgentName,
    model: str | None,
) -> None:
    """Upsert (model set) or delete (model=None) one (user, eng, role) row."""
    existing = session.execute(
        select(AgentModelPreference).where(
            AgentModelPreference.user_id == user_id,
            AgentModelPreference.engagement_id == engagement_id,
            AgentModelPreference.agent_role == role,
        )
    ).scalar_one_or_none()

    if model is None or model.strip() == "":
        if existing is not None:
            session.delete(existing)
        return

    now = datetime.now(UTC)
    if existing is None:
        session.add(
            AgentModelPreference(
                user_id=user_id,
                engagement_id=engagement_id,
                agent_role=role,
                model=model.strip(),
            )
        )
    else:
        existing.model = model.strip()
        existing.updated_at = now


# ---------------------------------------------------------------------------
# List / upsert / clear
# ---------------------------------------------------------------------------


@router.get("/agent-configurations", response_model=AgentConfigListResponse)
def list_agent_configurations(
    session: DbSession, user: CurrentNonGuestUser
) -> AgentConfigListResponse:
    rows = _rows_for_user(session, user.id)
    grouped = _grouped(rows)
    return AgentConfigListResponse(
        configurations=sorted(
            (_to_read(slug, by_role) for slug, by_role in grouped.items()),
            key=lambda r: r.engagement_slug,
        )
    )


@router.put("/agent-configurations/{slug}", response_model=AgentConfigRead)
def upsert_agent_configuration(
    slug: str,
    session: DbSession,
    user: CurrentNonGuestUser,
    body: AgentConfigPut,
) -> AgentConfigRead:
    eng = _engagement_by_slug(session, slug)

    # Missing keys leave the existing row untouched; explicit ``null``
    # clears that role. Distinguish the two via ``model_fields_set``
    # (pydantic v2) — a key that was never sent is not in the set.
    sent = body.model_fields_set
    mapping = {
        AgentName.strategic: body.strategic,
        AgentName.engagement_strategist: body.engagement_strategist,
        AgentName.tactical: body.tactical,
        AgentName.correlate: body.correlate,
    }
    for role, model in mapping.items():
        if not is_configurable_role(role.value):
            continue
        if role.value not in sent:
            # Field was omitted — leave any existing row for this role
            # untouched.
            continue
        # ``None`` clears; a present-but-empty string also clears.
        _upsert_one(
            session,
            user_id=user.id,
            engagement_id=eng.id,
            role=role,
            model=model,
        )
    session.commit()

    # Re-read to return the assembled row.
    rows = _rows_for_user(session, user.id)
    grouped = _grouped(rows)
    by_role = grouped.get(slug, {})
    if not by_role:
        return AgentConfigRead(
            engagement_id=eng.id,
            engagement_slug=slug,
            strategic=None,
            engagement_strategist=None,
            tactical=None,
            correlate=None,
            updated_at=None,
        )
    return _to_read(slug, by_role)


@router.delete("/agent-configurations/{slug}", status_code=204)
def clear_agent_configuration(
    slug: str,
    session: DbSession,
    user: CurrentNonGuestUser,
) -> Response:
    eng = _engagement_by_slug(session, slug)
    session.execute(
        delete(AgentModelPreference).where(
            AgentModelPreference.user_id == user.id,
            AgentModelPreference.engagement_id == eng.id,
        )
    )
    session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Export / import
# ---------------------------------------------------------------------------


@router.get("/agent-configurations/export")
def export_agent_configurations(session: DbSession, user: CurrentNonGuestUser) -> Response:
    """Download every config for the current user as a JSON file."""
    rows = _rows_for_user(session, user.id)
    grouped = _grouped(rows)

    configurations: dict[str, dict[str, str]] = {}
    for slug, by_role in grouped.items():
        payload: dict[str, str] = {}
        for role_key in (
            "strategic",
            "engagement_strategist",
            "tactical",
            "correlate",
        ):
            if role_key in by_role:
                payload[role_key] = by_role[role_key].model
        if payload:
            configurations[slug] = payload

    export = AgentConfigExport(
        version=1,
        exported_at=datetime.now(UTC),
        exported_by_user_id=user.id,
        configurations={
            slug: AgentConfigRolePayload(**payload) for slug, payload in configurations.items()
        },
    )
    body = export.model_dump_json(indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": ('attachment; filename="rtd-agent-configurations.json"'),
        },
    )


@router.post("/agent-configurations/import", response_model=AgentConfigImportResult)
def import_agent_configurations(
    session: DbSession,
    user: CurrentNonGuestUser,
    payload: AgentConfigExport,
) -> AgentConfigImportResult:
    """Apply an uploaded export. Unknown slugs are skipped, not fatal —
    an export from prod applied against a dev tenant may reference
    engagements that don't exist locally."""
    # Look up every referenced slug once.
    slugs = list(payload.configurations.keys())
    engagements = (
        session.execute(select(Engagement).where(Engagement.slug.in_(slugs))).scalars().all()
    )
    slug_to_eng = {e.slug: e for e in engagements}

    applied: list[str] = []
    skipped: list[str] = []
    for slug, role_payload in payload.configurations.items():
        eng = slug_to_eng.get(slug)
        if eng is None:
            skipped.append(slug)
            continue

        mapping = {
            AgentName.strategic: role_payload.strategic,
            AgentName.engagement_strategist: role_payload.engagement_strategist,
            AgentName.tactical: role_payload.tactical,
            AgentName.correlate: role_payload.correlate,
        }
        touched = False
        for role, model in mapping.items():
            if model is None:
                continue
            if not is_configurable_role(role.value):
                continue
            _upsert_one(
                session,
                user_id=user.id,
                engagement_id=eng.id,
                role=role,
                model=model,
            )
            touched = True

        if touched:
            applied.append(slug)
        else:
            # Slug matched but all three roles were null -> nothing to do.
            # We treat that as "skipped" rather than "applied" so the
            # analyst notices the empty rows.
            skipped.append(slug)

    session.commit()

    return AgentConfigImportResult(
        applied_slugs=applied,
        skipped_unknown_slugs=skipped,
    )
