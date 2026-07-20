"""Pure schema boundary tests for finding updates and generic imports."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.api.engagements import (
    MAX_FINDING_IMPORT_BATCH,
    MAX_FINDING_IMPORT_DETAILS_BYTES,
    FindingImport,
    FindingImportBatch,
)
from app.schemas.finding import (
    MAX_FINDING_SUMMARY_CHARS,
    MAX_FINDING_TAG_CHARS,
    MAX_FINDING_TAGS,
    FindingUpdate,
)


def _details_with_size(size: int) -> dict[str, str]:
    empty_size = len(json.dumps({"blob": ""}, separators=(",", ":")).encode())
    return {"blob": "x" * (size - empty_size)}


def _max_tags() -> list[str]:
    return [f"{index:02d}-" + "x" * 37 for index in range(MAX_FINDING_TAGS)]


def test_finding_update_exact_boundaries() -> None:
    value = FindingUpdate(
        title="t" * 300,
        summary="s" * MAX_FINDING_SUMMARY_CHARS,
        tags=_max_tags(),
    )
    assert len(value.title or "") == 300
    assert len(value.summary or "") == MAX_FINDING_SUMMARY_CHARS
    assert all(len(tag) == MAX_FINDING_TAG_CHARS for tag in value.tags or [])


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "x" * 301},
        {"title": "   "},
        {"summary": "x" * (MAX_FINDING_SUMMARY_CHARS + 1)},
        {"tags": ["x"] * (MAX_FINDING_TAGS + 1)},
        {"tags": ["x" * (MAX_FINDING_TAG_CHARS + 1)]},
    ],
)
def test_finding_update_rejects_max_plus_one_and_blank(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FindingUpdate.model_validate(payload)


def test_generic_import_exact_boundaries_and_caps() -> None:
    value = FindingImport(
        title="t" * 300,
        summary="s" * MAX_FINDING_SUMMARY_CHARS,
        source_tool="x" * 120,
        target="y" * 500,
        group_key="g" * 200,
        burp_serial_number="b" * 64,
        tags=_max_tags(),
        details=_details_with_size(MAX_FINDING_IMPORT_DETAILS_BYTES),
    )
    assert len(value.title) == 300

    invalid = [
        {"title": "x" * 301},
        {"title": "ok", "source_tool": "x" * 121},
        {"title": "ok", "target": "x" * 501},
        {"title": "ok", "group_key": "x" * 201},
        {"title": "ok", "burp_serial_number": "x" * 65},
        {"title": "ok", "tags": ["x"] * (MAX_FINDING_TAGS + 1)},
        {"title": "ok", "tags": ["x" * (MAX_FINDING_TAG_CHARS + 1)]},
        {
            "title": "ok",
            "details": _details_with_size(MAX_FINDING_IMPORT_DETAILS_BYTES + 1),
        },
    ]
    for payload in invalid:
        with pytest.raises(ValidationError):
            FindingImport.model_validate(payload)

    with pytest.raises(ValidationError):
        FindingImportBatch.model_validate([{"title": "x"}] * (MAX_FINDING_IMPORT_BATCH + 1))
