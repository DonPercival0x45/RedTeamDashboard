"""DarkWeb importer — Phase 10 (Dehashed JSON + CSV first slice).

Covers the pure parsers (no DB) and the upload endpoint integration
with the entities store + UPSERT merge semantics.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.main import app
from app.models import Engagement, EngagementStatus, Entity
from app.runs.streams import inbound_stream, outbound_stream
from app.services.darkweb_import import (
    parse_dehashed_csv,
    parse_dehashed_json,
)
from tests.test_engagements_api import _create, _headers


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def redis_client() -> Iterator[redis_lib.Redis]:
    r = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield r
    finally:
        r.close()


@pytest.fixture()
def cleanup_slugs(
    db: Session, redis_client: redis_lib.Redis
) -> Iterator[list[str]]:
    slugs: list[str] = []
    yield slugs
    for slug in slugs:
        eng_id = db.execute(
            select(Engagement.id).where(Engagement.slug == slug)
        ).scalar_one_or_none()
        if eng_id is None:
            continue
        db.execute(
            text("DELETE FROM approvals WHERE engagement_id = :id"),
            {"id": eng_id},
        )
        db.commit()
        db.execute(text("SELECT flush_engagement(:id)"), {"id": eng_id})
        db.commit()
        redis_client.delete(inbound_stream(eng_id), outbound_stream(eng_id))


@pytest.fixture()
def engagement(db: Session) -> Iterator[Engagement]:
    eng = Engagement(
        name="darkweb-test",
        slug=f"dw-{uuid.uuid4().hex[:8]}",
        status=EngagementStatus.active,
    )
    db.add(eng)
    db.commit()
    db.refresh(eng)
    try:
        yield eng
    finally:
        db.execute(
            text("DELETE FROM entities WHERE engagement_id = :id"),
            {"id": eng.id},
        )
        db.commit()
        db.execute(text("SELECT flush_engagement(:id)"), {"id": eng.id})
        db.commit()


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------


def test_parse_json_standard_api_shape() -> None:
    payload = json.dumps(
        {
            "balance": 100,
            "entries": [
                {
                    "id": "abc123",
                    "email": "victim@acme.test",
                    "password": "hunter2",
                    "database_name": "LinkedIn-2012",
                },
                {
                    "id": "def456",
                    "username": "vic_user",
                    "hashed_password": "$2a$10$...",
                    "database_name": "Adobe-2013",
                },
            ],
        }
    ).encode()
    result = parse_dehashed_json(payload, source_attribution="dump.json")
    assert result.total_rows == 2
    assert len(result.items) == 2
    by_value = {item.value: item for item in result.items}
    assert "victim@acme.test@LinkedIn-2012" in by_value
    assert "vic_user@Adobe-2013" in by_value
    # Properties preserve the original fields.
    li = by_value["victim@acme.test@LinkedIn-2012"]
    assert li.properties["email"] == "victim@acme.test"
    assert li.properties["password"] == "hunter2"
    assert li.properties["database_name"] == "LinkedIn-2012"
    assert li.properties["_source_attribution"] == "dump.json"
    # All entities classified as breach_record (per user-locked decision).
    assert all(item.type == "breach_record" for item in result.items)
    assert set(result.databases) == {"LinkedIn-2012", "Adobe-2013"}


def test_parse_json_bare_array_shape() -> None:
    payload = json.dumps(
        [{"email": "x@y.test", "database_name": "DB1"}]
    ).encode()
    result = parse_dehashed_json(payload)
    assert len(result.items) == 1
    assert result.items[0].value == "x@y.test@DB1"


def test_parse_json_skips_entry_without_identifier() -> None:
    payload = json.dumps(
        {
            "entries": [
                {"email": "ok@acme.test", "database_name": "DB1"},
                {"random_field": "no identifier here"},
            ]
        }
    ).encode()
    result = parse_dehashed_json(payload)
    assert len(result.items) == 1
    assert result.skipped_no_identifier == 1


def test_parse_json_falls_back_to_dehashed_id() -> None:
    """No email + no username + no database_name → use entry's id as the value."""
    payload = json.dumps(
        {"entries": [{"id": "row-99", "password": "x"}]}
    ).encode()
    result = parse_dehashed_json(payload)
    assert len(result.items) == 1
    assert result.items[0].value == "dehashed:row-99"


def test_parse_json_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="invalid Dehashed JSON"):
        parse_dehashed_json(b"{not really json")


def test_parse_json_rejects_missing_entries_key() -> None:
    with pytest.raises(ValueError, match="missing top-level 'entries'"):
        parse_dehashed_json(json.dumps({"balance": 100}).encode())


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------


_CSV_HEADER = "id,email,username,password,hashed_password,database_name"


def _csv(rows: list[str]) -> bytes:
    body = "\n".join([_CSV_HEADER] + rows) + "\n"
    return body.encode()


def test_parse_csv_happy_path() -> None:
    payload = _csv(
        [
            "abc,victim@acme.test,,pw,,LinkedIn-2012",
            "def,,otheruser,,$2a$10$...,Adobe-2013",
        ]
    )
    result = parse_dehashed_csv(payload, source_attribution="dump.csv")
    assert result.total_rows == 2
    values = {item.value for item in result.items}
    assert values == {
        "victim@acme.test@LinkedIn-2012",
        "otheruser@Adobe-2013",
    }
    assert set(result.databases) == {"LinkedIn-2012", "Adobe-2013"}


def test_parse_csv_tolerates_utf8_bom() -> None:
    payload = b"\xef\xbb\xbf" + _csv(
        ["abc,x@y.test,,,,DB1"]
    )
    result = parse_dehashed_csv(payload)
    assert len(result.items) == 1


def test_parse_csv_rejects_missing_header() -> None:
    with pytest.raises(ValueError, match="missing header"):
        parse_dehashed_csv(b"")


# ---------------------------------------------------------------------------
# Endpoint integration
# ---------------------------------------------------------------------------


def test_import_endpoint_persists_dehashed_json(
    client: TestClient,
    db: Session,
    cleanup_slugs: list[str],
) -> None:
    eng = _create(client, "dw import json")
    cleanup_slugs.append(eng["slug"])

    payload = json.dumps(
        {
            "entries": [
                {
                    "email": "victim@acme.test",
                    "password": "hunter2",
                    "database_name": "LinkedIn-2012",
                },
                {
                    "username": "alt_user",
                    "hashed_password": "$2a$10$...",
                    "database_name": "Adobe-2013",
                },
            ]
        }
    ).encode()
    res = client.post(
        f"/engagements/{eng['slug']}/entities/import/darkweb?source=dehashed",
        files={"file": ("dump.json", payload, "application/json")},
        headers=_headers(),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["source"] == "dehashed"
    assert body["inserted"] == 2
    assert body["merged"] == 0
    assert body["total_rows"] == 2
    assert sorted(body["databases"]) == ["Adobe-2013", "LinkedIn-2012"]

    persisted = list(
        db.execute(
            select(Entity).where(Entity.engagement_id == uuid.UUID(eng["id"]))
        ).scalars()
    )
    assert len(persisted) == 2
    for e in persisted:
        assert e.type == "breach_record"
        assert e.source_tool == "dehashed_import"


def test_import_endpoint_persists_dehashed_csv(
    client: TestClient,
    db: Session,
    cleanup_slugs: list[str],
) -> None:
    eng = _create(client, "dw import csv")
    cleanup_slugs.append(eng["slug"])

    csv_payload = _csv(
        [
            "abc,victim@acme.test,,pw,,LinkedIn-2012",
        ]
    )
    res = client.post(
        f"/engagements/{eng['slug']}/entities/import/darkweb?source=dehashed",
        files={"file": ("dump.csv", csv_payload, "text/csv")},
        headers=_headers(),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["inserted"] == 1
    assert body["databases"] == ["LinkedIn-2012"]


def test_reimport_merges_breach_record(
    client: TestClient,
    db: Session,
    cleanup_slugs: list[str],
) -> None:
    """Same (email, database_name) re-imported with new metadata merges
    into the existing row via UPSERT — count stays at 1 row, properties
    pick up the new keys."""
    eng = _create(client, "dw merge")
    cleanup_slugs.append(eng["slug"])

    first = json.dumps(
        {
            "entries": [
                {
                    "email": "victim@acme.test",
                    "password": "hunter2",
                    "database_name": "LinkedIn-2012",
                }
            ]
        }
    ).encode()
    res1 = client.post(
        f"/engagements/{eng['slug']}/entities/import/darkweb?source=dehashed",
        files={"file": ("first.json", first, "application/json")},
        headers=_headers(),
    )
    assert res1.json()["inserted"] == 1

    second = json.dumps(
        {
            "entries": [
                {
                    "email": "victim@acme.test",
                    "password": "hunter2",
                    "hashed_password": "$2a$10$discovered_later",
                    "database_name": "LinkedIn-2012",
                }
            ]
        }
    ).encode()
    res2 = client.post(
        f"/engagements/{eng['slug']}/entities/import/darkweb?source=dehashed",
        files={"file": ("second.json", second, "application/json")},
        headers=_headers(),
    )
    body2 = res2.json()
    assert body2["merged"] == 1
    assert body2["inserted"] == 0

    rows = list(
        db.execute(
            select(Entity).where(Entity.engagement_id == uuid.UUID(eng["id"]))
        ).scalars()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.properties["hashed_password"] == "$2a$10$discovered_later"


def test_import_endpoint_rejects_unsupported_source(
    client: TestClient,
    cleanup_slugs: list[str],
) -> None:
    eng = _create(client, "dw unsupported")
    cleanup_slugs.append(eng["slug"])

    res = client.post(
        f"/engagements/{eng['slug']}/entities/import/darkweb?source=spycloud",
        files={"file": ("x.json", b"{}", "application/json")},
        headers=_headers(),
    )
    assert res.status_code == 400
    assert "unsupported" in res.json()["detail"]


def test_import_endpoint_rejects_malformed_payload(
    client: TestClient,
    cleanup_slugs: list[str],
) -> None:
    eng = _create(client, "dw malformed")
    cleanup_slugs.append(eng["slug"])

    res = client.post(
        f"/engagements/{eng['slug']}/entities/import/darkweb?source=dehashed",
        files={"file": ("x.json", b"{this is not json", "application/json")},
        headers=_headers(),
    )
    assert res.status_code == 400
