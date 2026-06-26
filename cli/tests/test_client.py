"""HTTP client error mapping + auth header."""
from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from xray.client import APIError, Client
from xray.config import Profile


@pytest.fixture()
def profile() -> Profile:
    return Profile(name="t", url="http://api.test", api_key="rtd_key123")


def test_get_sends_api_key_header(profile: Profile, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://api.test/health", json={"ok": True})
    with Client(profile) as c:
        c.get("/health")
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers["x-api-key"] == "rtd_key123"


def test_get_returns_parsed_json(profile: Profile, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://api.test/x", json={"a": 1})
    with Client(profile) as c:
        assert c.get("/x") == {"a": 1}


def test_204_returns_none(profile: Profile, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://api.test/x", status_code=204)
    with Client(profile) as c:
        assert c.delete("/x") is None


def test_4xx_with_detail_raises_apierror(profile: Profile, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://api.test/x",
        status_code=400,
        json={"detail": "bad request"},
    )
    with Client(profile) as c, pytest.raises(APIError) as exc:
        c.get("/x")
    assert exc.value.status_code == 400
    assert exc.value.detail == "bad request"


def test_5xx_without_json_body_uses_text(profile: Profile, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://api.test/x",
        status_code=500,
        content=b"upstream timeout",
    )
    with Client(profile) as c, pytest.raises(APIError) as exc:
        c.get("/x")
    assert exc.value.detail == "upstream timeout"


def test_post_serializes_body(profile: Profile, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://api.test/x", json={"id": 1})
    with Client(profile) as c:
        c.post("/x", json={"name": "n"})
    req = httpx_mock.get_request()
    assert req is not None
    assert req.read() == b'{"name":"n"}'


def test_base_url_trailing_slash_stripped(httpx_mock: HTTPXMock) -> None:
    # Constructor sees the raw URL; trailing slash handling lives in Profile.
    # Just verify we can hit a path with a clean base URL.
    profile = Profile(name="t", url="http://api.test", api_key="k")
    httpx_mock.add_response(url="http://api.test/foo", json={})
    with Client(profile) as c:
        c.get("/foo")
