"""Server-side static map renderer for the Dossier PDF export (v2.23.0).

WeasyPrint can't drive JavaScript, so the browser-side Leaflet map on
the Dossier tab has no direct path into the PDF. This module renders
the same points into a plain PNG using ``staticmap`` (pure Python +
Pillow — no extra system deps beyond what WeasyPrint already brings).
Callers base64-embed the PNG into the report HTML.

OSM tile usage: staticmap defaults to https://a.tile.openstreetmap.org/
which is fine for the ~4-16 tiles a single report needs. We stay well
under OSM's per-user policy limit. No API key required.
"""
from __future__ import annotations

import io
import logging
from collections.abc import Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DossierMapPoint:
    lat: float
    lon: float
    label: str = ""  # currently unused by the renderer; kept for callers


def render_dossier_map(
    points: Sequence[DossierMapPoint],
    *,
    width: int = 900,
    height: int = 500,
    marker_radius: int = 8,
    marker_color: str = "#e11d48",  # rose-600 — matches the app's accent
) -> bytes:
    """Render the given points onto a world map. Returns PNG bytes, or
    ``b''`` if there's nothing to draw or rendering fails.

    Failure modes swallowed:
      - ``staticmap`` import missing (dev env drift)
      - Tile fetch timeout (offline or OSM 4xx / 5xx)
      - Any Pillow / rendering error
    Callers should treat ``b''`` as "no map available" and skip the section.
    """
    if not points:
        return b""
    try:
        from staticmap import CircleMarker, StaticMap
    except ImportError:
        logger.warning("staticmap not installed — skipping dossier map render")
        return b""

    try:
        m = StaticMap(width, height, url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")
        for p in points:
            m.add_marker(CircleMarker((p.lon, p.lat), marker_color, marker_radius))
        image = m.render()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:  # pragma: no cover — network-dependent
        logger.warning("dossier map render failed: %s", exc)
        return b""
