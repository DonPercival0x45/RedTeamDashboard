"""Integration-ish tests over the click commands using CliRunner + pytest-httpx.

Tests construct a real ``Context`` via the CLI invocation and assert on
exit code, stdout, and the recorded HTTP traffic.
"""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from pytest_httpx import HTTPXMock

from rtd.config import Config
from rtd.main import cli


def _invoke(args: list[str], config_path: Path) -> str:
    runner = CliRunner()
    result = runner.invoke(cli, ["--config", str(config_path), *args])
    assert result.exit_code == 0, result.output + (str(result.exception) if result.exception else "")
    return result.output


# ---------------------------------------------------------------------------
# login + profile
# ---------------------------------------------------------------------------


def test_login_creates_default_profile_for_first_login(config_path: Path) -> None:
    _invoke(
        ["login", "--profile", "personal", "--url", "http://api", "--key", "rtd_x"],
        config_path,
    )
    cfg = Config.load(config_path)
    assert cfg.default == "personal"
    assert cfg.profiles["personal"].url == "http://api"


def test_login_does_not_change_default_for_subsequent(config_path: Path) -> None:
    _invoke(["login", "--profile", "a", "--url", "http://a", "--key", "k"], config_path)
    _invoke(["login", "--profile", "b", "--url", "http://b", "--key", "k"], config_path)
    assert Config.load(config_path).default == "a"


def test_login_default_flag_overrides(config_path: Path) -> None:
    _invoke(["login", "--profile", "a", "--url", "http://a", "--key", "k"], config_path)
    _invoke(["login", "--profile", "b", "--url", "http://b", "--key", "k", "--default"],
            config_path)
    assert Config.load(config_path).default == "b"


def test_profile_list_json_includes_default_marker(seeded_config: Config) -> None:
    out = _invoke(["--json", "profile", "list"], seeded_config.path)
    rows = json.loads(out)
    assert len(rows) == 1
    assert rows[0] == {"name": "local", "url": "http://localhost:8000", "default": True}


def test_profile_use_switches_default(seeded_config: Config) -> None:
    _invoke(["login", "--profile", "other", "--url", "http://o", "--key", "k"],
            seeded_config.path)
    _invoke(["profile", "use", "other"], seeded_config.path)
    assert Config.load(seeded_config.path).default == "other"


# ---------------------------------------------------------------------------
# engagement
# ---------------------------------------------------------------------------


def test_engagement_list_uses_default_profile(
    seeded_config: Config,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="http://localhost:8000/engagements",
        json=[{"id": "1", "slug": "acme", "name": "Acme",
               "status": "active", "created_at": "2026-06-02T00:00:00Z"}],
    )
    out = _invoke(["--json", "engagement", "list"], seeded_config.path)
    rows = json.loads(out)
    assert rows[0]["slug"] == "acme"
    # Verify auth header reached the mock.
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers["x-api-key"] == "rtd_test"


def test_engagement_create_posts_name(
    seeded_config: Config,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="http://localhost:8000/engagements",
        json={"id": "1", "slug": "new", "name": "New",
              "status": "active", "created_at": "2026-06-02T00:00:00Z"},
    )
    _invoke(["--json", "engagement", "create", "--name", "New"], seeded_config.path)
    req = httpx_mock.get_request()
    assert req is not None
    assert req.method == "POST"
    assert json.loads(req.read()) == {"name": "New"}


# ---------------------------------------------------------------------------
# run start
# ---------------------------------------------------------------------------


def test_run_start_without_model_omits_model_in_body(
    seeded_config: Config,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="http://localhost:8000/engagements/acme/runs",
        json={"engagement_id": "1", "thread_id": "t1",
              "events_stream": "runs:1:events",
              "model": {"provider": "anthropic", "name": "claude-opus-4-7"}},
    )
    _invoke(["--json", "run", "start", "acme", "-p", "go", "--no-tail"],
            seeded_config.path)
    req = httpx_mock.get_request()
    assert req is not None
    body = json.loads(req.read())
    assert body == {"prompt": "go"}


def test_run_start_with_model_includes_model_in_body(
    seeded_config: Config,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="http://localhost:8000/engagements/acme/runs",
        json={"engagement_id": "1", "thread_id": "t1",
              "events_stream": "runs:1:events",
              "model": {"provider": "openai", "name": "gpt-4o-mini"}},
    )
    _invoke(
        ["--json", "run", "start", "acme", "-p", "go",
         "--provider", "openai", "--model", "gpt-4o-mini", "--no-tail"],
        seeded_config.path,
    )
    body = json.loads(httpx_mock.get_request().read())
    assert body["model"] == {"provider": "openai", "name": "gpt-4o-mini"}


def test_run_start_provider_without_model_fails(seeded_config: Config) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--config", str(seeded_config.path), "run", "start", "acme",
         "-p", "go", "--provider", "openai", "--no-tail"],
    )
    assert result.exit_code != 0
    assert "together" in result.output


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


def test_approve_defaults_to_approved_true(
    seeded_config: Config,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="http://localhost:8000/approvals/abc/decision",
        json={"status": "approved", "tool_name": "portscan", "decided_at": "..."},
    )
    _invoke(["--json", "approve", "abc"], seeded_config.path)
    body = json.loads(httpx_mock.get_request().read())
    assert body == {"approved": True}


def test_approve_deny_flag(
    seeded_config: Config,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="http://localhost:8000/approvals/abc/decision",
        json={"status": "denied", "tool_name": "portscan", "decided_at": "..."},
    )
    _invoke(["--json", "approve", "abc", "--deny", "--reason", "out of window"],
            seeded_config.path)
    body = json.loads(httpx_mock.get_request().read())
    assert body == {"approved": False, "reason": "out of window"}


def test_approve_remember_and_deny_conflict(seeded_config: Config) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--config", str(seeded_config.path), "approve", "abc",
         "--deny", "--remember"],
    )
    assert result.exit_code != 0
    assert "remember" in result.output.lower()


# ---------------------------------------------------------------------------
# ssh URL parsing (no actual exec)
# ---------------------------------------------------------------------------


def test_ssh_derives_app_and_rg_from_profile_url() -> None:
    from rtd.commands.ssh import _app_from_url, _default_rg

    url = "https://rtd-prod-backend.purplebeach-xx.centralus.azurecontainerapps.io"
    app = _app_from_url(url)
    assert app == "rtd-prod-backend"
    assert _default_rg(app) == "rtd-prod"


def test_ssh_unparseable_url_returns_none() -> None:
    from rtd.commands.ssh import _app_from_url

    # The regex requires at least one DNS label followed by '.', so a host
    # without a dot doesn't yield an app name.
    assert _app_from_url("not-a-url") is None
    assert _app_from_url("https://") is None
