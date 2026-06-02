"""HTTP client wrapper around one :class:`~rtd.config.Profile`.

Centralizes auth header, base URL, and error-to-exception translation so each
command file is a thin click wrapper. SSE streaming is its own helper
(:func:`stream_events`) since the lifetime + framing don't match request/response.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from httpx_sse import connect_sse

from rtd.config import Profile

_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class APIError(Exception):
    """HTTP error from the backend.

    Carries the status code and parsed detail so commands can render a
    one-liner without the user seeing a stack trace for a normal 4xx.
    """

    def __init__(self, status_code: int, detail: str, *, method: str, url: str) -> None:
        super().__init__(f"{method} {url} -> {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class Client:
    """Thin httpx wrapper that always sends X-API-Key + parses errors uniformly."""

    def __init__(self, profile: Profile, *, timeout: httpx.Timeout | None = None) -> None:
        self._profile = profile
        self._http = httpx.Client(
            base_url=profile.url,
            headers={"X-API-Key": profile.api_key},
            timeout=timeout or _DEFAULT_TIMEOUT,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def profile(self) -> Profile:
        return self._profile

    # ------------------------------------------------------------------
    # HTTP verbs
    # ------------------------------------------------------------------

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._json(self._http.get(path, **kwargs))

    def post(self, path: str, *, json: Any | None = None, **kwargs: Any) -> Any:
        return self._json(self._http.post(path, json=json, **kwargs))

    def patch(self, path: str, *, json: Any | None = None, **kwargs: Any) -> Any:
        return self._json(self._http.patch(path, json=json, **kwargs))

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self._json(self._http.delete(path, **kwargs))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _json(self, response: httpx.Response) -> Any:
        if 200 <= response.status_code < 300:
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        detail = _extract_detail(response)
        raise APIError(
            response.status_code,
            detail,
            method=response.request.method,
            url=str(response.request.url),
        )


def _extract_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or "(no body)"
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


@contextmanager
def stream_events(
    profile: Profile,
    path: str,
    *,
    params: dict[str, str] | None = None,
    last_event_id: str | None = None,
) -> Iterator[Iterator[dict[str, Any]]]:
    """SSE consumer for the run-events stream.

    Yields a generator of parsed event dicts: ``{"id", "event", "data"}``. Use
    inside a ``with`` so the underlying connection closes when the consumer
    breaks out of the loop.
    """
    headers = {"X-API-Key": profile.api_key, "Accept": "text/event-stream"}
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    # No idle timeout on read — SSE streams are intentionally long-lived.
    timeout = httpx.Timeout(None, connect=10.0)
    with (
        httpx.Client(base_url=profile.url, headers=headers, timeout=timeout) as http,
        connect_sse(http, "GET", path, params=params) as event_source,
    ):
        def _iter() -> Iterator[dict[str, Any]]:
            for sse in event_source.iter_sse():
                yield {
                    "id": sse.id,
                    "event": sse.event,
                    "data": sse.json() if sse.data else None,
                }
        yield _iter()
