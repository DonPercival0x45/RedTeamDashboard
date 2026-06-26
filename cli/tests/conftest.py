"""Shared pytest fixtures for CLI tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from xray.config import Config, Profile


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    """Disposable per-test config path under tmp_path."""
    return tmp_path / "config.toml"


@pytest.fixture()
def seeded_config(config_path: Path) -> Config:
    """Config with one profile already saved + marked default."""
    cfg = Config(path=config_path, default=None, profiles={})
    cfg.upsert(Profile(name="local", url="http://localhost:8000", api_key="rtd_test"),
               make_default=True)
    cfg.save()
    return cfg
