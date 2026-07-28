"""Maltego .mtgl (Lucene-backed) graph import — v3.6.0.

Maltego 4.11 shipped a new graph container: instead of a zip-of-GraphML
(the old ``.mtgx``), the ``.mtgl`` archive holds an Apache Lucene 5.x
index under ``Graphs/Graph1/DataEntities/``. Property values live in
the Lucene stored-fields codec — not extractable from Python without
reimplementing Lucene.

We shell out to a Java sidecar (``backend/tools/mtgl2json``) that opens
the Lucene index with lucene-core + lucene-backward-codecs and emits a
compact JSON blob. This module invokes the sidecar and reduces its
output to the shared :class:`ParseResult` shape that the existing
``.mtgx`` importer produces, so the API + persistence layer stays
unchanged.

Charter posture: same as .mtgx — analyst runs Maltego on their own infra
and uploads the resulting graph. We don't call Maltego or shell out to
anything but our own signed sidecar.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

from app.services.maltego_import import (
    _TYPE_NORMALIZE,
    _VALUE_PROPERTY_FOR_TYPE,
    ParsedEntity,
    ParseResult,
)

# The sidecar's runtime location inside the container. Overrideable via
# env var so tests / dev can point at a bare Maven build tree.
MTGL2JSON_JAR = os.environ.get("MTGL2JSON_JAR", "/opt/mtgl2json/mtgl2json.jar")
JAVA_BIN = os.environ.get("JAVA_BIN", "java")

# Hard cap on the sidecar's wall-clock. A large graph (thousands of
# entities) still finishes in under a second on a warm JVM; 60s is
# absurd but cheap insurance against a runaway.
_SIDECAR_TIMEOUT_SECONDS = 60

# The .mtgl zip is a full Lucene index plus icon PNGs, so a bounded
# reasonable graph tops out around a few MB. Reject anything absurd
# up front so we never write it to disk.
_MAX_MTGL_BYTES = 200 * 1024 * 1024  # 200 MB

# The internal fingerprint that distinguishes .mtgl from .mtgx.
_MTGL_MARKER_PREFIX = "Graphs/Graph1/DataEntities/"


def looks_like_mtgl(zip_bytes: bytes) -> bool:
    """Cheap sniff: does the zip contain the Lucene index directory the
    sidecar reads? Used by the API dispatcher to route .mtgl vs .mtgx
    without trusting the filename."""
    if not zipfile.is_zipfile(io.BytesIO(zip_bytes)):
        return False
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return any(n.startswith(_MTGL_MARKER_PREFIX) for n in zf.namelist())


def parse_mtgl(
    zip_bytes: bytes, source_attribution: str = "", *, jar_path: str | None = None
) -> ParseResult:
    """Parse a Maltego ``.mtgl`` graph via the Java Lucene sidecar.

    Signature mirrors :func:`app.services.maltego_import.parse_mtgx` so
    the API layer can dispatch without special-casing return handling.
    """
    if len(zip_bytes) > _MAX_MTGL_BYTES:
        raise ValueError(
            f".mtgl archive too large ({len(zip_bytes)} bytes; max {_MAX_MTGL_BYTES})"
        )
    if not zipfile.is_zipfile(io.BytesIO(zip_bytes)):
        raise ValueError("not a .mtgl zip archive")

    jar = jar_path or MTGL2JSON_JAR
    if not Path(jar).is_file():
        raise RuntimeError(
            f"mtgl2json sidecar JAR not found at {jar}. "
            "Backend container may be missing the sidecar build."
        )

    with tempfile.NamedTemporaryFile(suffix=".mtgl", delete=False) as f:
        f.write(zip_bytes)
        tmp_path = f.name
    try:
        completed = subprocess.run(
            [JAVA_BIN, "-jar", jar, tmp_path],
            capture_output=True,
            timeout=_SIDECAR_TIMEOUT_SECONDS,
        )
    finally:
        os.unlink(tmp_path)

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")[:2000]
        raise ValueError(f"mtgl2json sidecar failed (exit {completed.returncode}): {stderr}")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"mtgl2json produced invalid JSON: {exc}") from exc

    raw_entities = payload.get("entities", [])
    return _reduce_entities(raw_entities, source_attribution)


def _reduce_entities(raw: list[dict], source_attribution: str) -> ParseResult:
    """Turn the sidecar's ``[{type, fields:[[name, value], ...]}, ...]``
    into the ``ParseResult`` the API + persistence layer already consumes.

    Field-name convention from the sidecar:
      - ``valueStr[string]``      → the entity's canonical display value
      - ``type[string]``          → the raw Maltego type
      - ``id[string]``            → the graph-internal id
      - ``properties[map]|<propKey>[map]|value:<t>[<t>]`` → real property values
      - ``properties[map]|<propKey>[map]|displayName[string]`` → property label

    We prefer ``valueStr`` because it matches what the analyst sees in
    Maltego's canvas; fall back to the property listed in
    ``_VALUE_PROPERTY_FOR_TYPE`` if ``valueStr`` is missing.
    """
    items: list[ParsedEntity] = []
    skipped_empty = 0
    skipped_unknown = 0

    for raw_ent in raw:
        maltego_type = (raw_ent.get("type") or "").strip()
        if not maltego_type:
            skipped_unknown += 1
            continue

        fields = raw_ent.get("fields") or []
        # First pass: dedupe the flat list into a working dict, and pull
        # out ``properties[map]|X|value:…`` sub-fields as clean props.
        raw_fields: dict[str, str] = {}
        clean_props: dict[str, str] = {}
        for pair in fields:
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            name, value = pair
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            bare_name = _strip_type_suffix(name)
            raw_fields[bare_name] = value

            # properties[map]|<key>[map]|value:<t>[<t>] → clean_props[<key>] = value
            if name.startswith("properties[map]|") and "|value:" in name:
                key_segment = name[len("properties[map]|") :]
                key = key_segment.split("[map]|", 1)[0].replace("°", ".")
                clean_props[key] = value

        value = raw_fields.get("valueStr") or ""
        if not value:
            # Fallback to the canonical property registered for this type
            canonical_prop = _VALUE_PROPERTY_FOR_TYPE.get(maltego_type)
            if canonical_prop and canonical_prop in clean_props:
                value = clean_props[canonical_prop]

        if not value:
            skipped_empty += 1
            continue

        normalized_type = _TYPE_NORMALIZE.get(maltego_type, maltego_type)

        # Attribution + raw id survive as metadata for round-trip debug.
        clean_props.setdefault("_source_attribution", source_attribution)
        raw_id = raw_fields.get("id")
        if raw_id:
            clean_props["_maltego_id"] = raw_id

        items.append(
            ParsedEntity(
                type=normalized_type,
                value=value,
                properties=clean_props,
                maltego_type=maltego_type,
            )
        )

    return ParseResult(
        items=items,
        skipped_empty=skipped_empty,
        skipped_unknown=skipped_unknown,
        total_nodes=len(raw),
    )


def _strip_type_suffix(name: str) -> str:
    """``"type[string]"`` → ``"type"``. The sidecar preserves the Lucene
    field-type suffix so the raw dump is round-trippable; we don't need
    it downstream."""
    idx = name.find("[")
    return name if idx < 0 else name[:idx]
