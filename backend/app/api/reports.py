"""PDF engagement report.

GET /engagements/{slug}/report -> application/pdf

Pulls engagement + scope + findings + approvals + audit_log from the DB,
renders the Jinja2 template, hands HTML to WeasyPrint for PDF rendering.
The template (``app/templates/report.html``) is where layout + styling
live; this endpoint is just the wiring.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import (
    Approval,
    AuditLog,
    Engagement,
    Finding,
    FindingStatus,
    Observation,
    ScopeItem,
)
from app.schemas.report import ReportReadiness
from app.services.dossier_map_render import DossierMapPoint, render_dossier_map
from app.services.report_readiness import build_report_readiness

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


@dataclass
class _DossierEntry:
    """One row per IP in the report's Geographic footprint section.
    Merges freeipapi (geo) + ipinfo (ASN/hosting) — mirrors the frontend
    DossierView shape so the report matches what the analyst sees on-screen.
    """

    ip: str
    location: str = ""
    asn: str = ""
    asn_name: str = ""
    is_hosting: bool = False
    flags: list[str] = field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None


def _build_dossier_entries(findings: list[Finding]) -> list[_DossierEntry]:
    """Extract merged IP-intel rows from freeipapi + ipinfo findings.

    Same merge semantics as ``frontend/components/dossier-view.tsx``:
    one entry per IP, geo prefers freeipapi (richer country strings),
    flags union across both sources.
    """
    entries: dict[str, _DossierEntry] = {}
    for f in findings:
        if f.source_tool not in ("freeipapi", "ipinfo"):
            continue
        items = (f.details or {}).get("items") if isinstance(f.details, dict) else None
        if not isinstance(items, list) or not items:
            continue
        item = items[0]
        if not isinstance(item, dict):
            continue
        ip = str(item.get("ip") or f.target or "").strip()
        if not ip:
            continue
        entry = entries.setdefault(ip, _DossierEntry(ip=ip))

        loc_parts = [
            item.get("city_name"),
            item.get("region_name"),
            item.get("country_name") or item.get("country_code"),
        ]
        loc = ", ".join(str(p).strip() for p in loc_parts if p)
        if loc and not entry.location:
            entry.location = loc

        lat = _coerce_float(item.get("latitude"))
        lon = _coerce_float(item.get("longitude"))
        if entry.latitude is None and lat is not None:
            entry.latitude = lat
        if entry.longitude is None and lon is not None:
            entry.longitude = lon

        if not entry.asn and item.get("asn"):
            entry.asn = str(item["asn"])
        if not entry.asn_name and item.get("asn_name"):
            entry.asn_name = str(item["asn_name"])
        if item.get("is_hosting") is True:
            entry.is_hosting = True

        for flag in ("is_proxy", "is_vpn", "is_tor", "is_mobile"):
            label = flag.removeprefix("is_")
            if item.get(flag) is True and label not in entry.flags:
                entry.flags.append(label)

    return sorted(entries.values(), key=lambda e: _ip_sort_key(e.ip))


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ip_sort_key(ip: str) -> tuple:
    """Sort IPv4 numerically, IPv6 lexicographically. Cheap best-effort."""
    parts = ip.split(".")
    if len(parts) == 4:
        try:
            return (0, tuple(int(p) for p in parts))
        except ValueError:
            pass
    return (1, ip)


@router.get(
    "/engagements/{slug}/report/readiness",
    response_model=ReportReadiness,
)
def engagement_report_readiness(
    slug: str,
    session: DbSession,
    _user: CurrentUser,
) -> ReportReadiness:
    engagement = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if engagement is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return build_report_readiness(session, engagement=engagement)


@router.get(
    "/engagements/{slug}/report",
    responses={200: {"content": {"application/pdf": {}}}},
)
def engagement_report(
    slug: str,
    session: DbSession,
    user: CurrentUser,  # noqa: ARG001 — gates the endpoint
    omit_excluded: Annotated[
        bool,
        Query(
            description=(
                "Drop findings marked out_of_scope / outside_roe from the "
                "PDF. Defaults true so an unqualified download is client-safe; "
                "set false only for an explicit internal report."
            ),
        ),
    ] = True,
) -> Response:
    eng = session.execute(
        select(Engagement).where(Engagement.slug == slug)
    ).scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail="engagement not found")

    scope_items = list(
        session.execute(
            select(ScopeItem)
            .where(ScopeItem.engagement_id == eng.id)
            .order_by(ScopeItem.created_at)
        ).scalars()
    )
    # Only validated findings are report-eligible (Phase 8 validation gate).
    # v1.4.0: analyst-set exclusion also drops the row when the caller
    # asks for a client-clean report.
    findings_stmt = select(Finding).where(
        Finding.engagement_id == eng.id,
        Finding.status == FindingStatus.validated,
        Finding.deleted_at.is_(None),
    )
    if omit_excluded:
        findings_stmt = findings_stmt.where(Finding.exclusion.is_(None))
    findings = list(
        session.execute(findings_stmt.order_by(Finding.created_at.desc())).scalars()
    )
    approvals = list(
        session.execute(
            select(Approval)
            .where(Approval.engagement_id == eng.id)
            .order_by(Approval.created_at.desc())
        ).scalars()
    )
    observations = list(
        session.execute(
            select(Observation)
            .where(Observation.engagement_id == eng.id)
            .order_by(Observation.created_at)
        ).scalars()
    )
    audit = list(
        session.execute(
            select(AuditLog)
            .where(AuditLog.engagement_id == eng.id)
            .order_by(AuditLog.created_at)
        ).scalars()
    )

    # v2.23.0: Dossier section. Extract merged IP intel from freeipapi +
    # ipinfo findings, render a static PNG world map with pins, and pass
    # both to the template. Base64 data-URI keeps the image self-contained
    # so WeasyPrint doesn't need network access during PDF generation.
    dossier_entries = _build_dossier_entries(findings)
    dossier_points = [
        DossierMapPoint(lat=e.latitude, lon=e.longitude, label=e.ip)
        for e in dossier_entries
        if e.latitude is not None and e.longitude is not None
    ]
    dossier_map_png = render_dossier_map(dossier_points)
    dossier_map_data_uri = (
        f"data:image/png;base64,{base64.b64encode(dossier_map_png).decode('ascii')}"
        if dossier_map_png
        else ""
    )

    template = _env.get_template("report.html")
    html = template.render(
        engagement=eng,
        scope_items=scope_items,
        findings=findings,
        observations=observations,
        approvals=approvals,
        audit=audit,
        generated_at=datetime.now(tz=UTC),
        dossier_entries=dossier_entries,
        dossier_map_data_uri=dossier_map_data_uri,
    )

    from weasyprint import HTML  # deferred: needs GTK, not available on all hosts

    pdf_bytes = HTML(string=html).write_pdf()
    profile = "client" if omit_excluded else "internal"
    filename = (
        f"{eng.slug}-{profile}-report-"
        f"{datetime.now(tz=UTC).strftime('%Y%m%d')}.pdf"
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
