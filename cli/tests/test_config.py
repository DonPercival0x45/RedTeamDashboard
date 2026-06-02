"""Config TOML round-trip + invariants."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from rtd.config import Config, ConfigError, Profile


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path / "absent.toml")
    assert cfg.default is None
    assert cfg.profiles == {}


def test_upsert_sets_default_for_first_profile(tmp_path: Path) -> None:
    cfg = Config(path=tmp_path / "c.toml", default=None, profiles={})
    cfg.upsert(Profile(name="a", url="http://x", api_key="k"))
    assert cfg.default == "a"


def test_upsert_keeps_default_for_subsequent(tmp_path: Path) -> None:
    cfg = Config(path=tmp_path / "c.toml", default=None, profiles={})
    cfg.upsert(Profile(name="a", url="http://x", api_key="k"))
    cfg.upsert(Profile(name="b", url="http://y", api_key="k"))
    assert cfg.default == "a"


def test_upsert_make_default_overrides(tmp_path: Path) -> None:
    cfg = Config(path=tmp_path / "c.toml", default=None, profiles={})
    cfg.upsert(Profile(name="a", url="http://x", api_key="k"))
    cfg.upsert(Profile(name="b", url="http://y", api_key="k"), make_default=True)
    assert cfg.default == "b"


def test_save_then_load_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    original = Config(path=path, default="a", profiles={
        "a": Profile(name="a", url="http://x", api_key="rtd_aaa"),
        "b": Profile(name="b", url="https://example.com", api_key="rtd_bbb"),
    })
    original.save()
    reloaded = Config.load(path)
    assert reloaded.default == "a"
    assert reloaded.profiles["a"].url == "http://x"
    assert reloaded.profiles["b"].api_key == "rtd_bbb"


def test_save_writes_0600_perms(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    cfg = Config(path=path, default=None, profiles={})
    cfg.upsert(Profile(name="a", url="http://x", api_key="k"))
    cfg.save()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"expected 0600, got {mode:#o}"


def test_resolve_named_profile(tmp_path: Path) -> None:
    cfg = Config(path=tmp_path / "c.toml", default=None, profiles={})
    cfg.upsert(Profile(name="a", url="http://x", api_key="k"))
    cfg.upsert(Profile(name="b", url="http://y", api_key="k"))
    assert cfg.resolve("b").name == "b"


def test_resolve_default_when_no_name(tmp_path: Path) -> None:
    cfg = Config(path=tmp_path / "c.toml", default="a", profiles={
        "a": Profile(name="a", url="http://x", api_key="k"),
    })
    assert cfg.resolve(None).name == "a"


def test_resolve_unknown_profile_raises(tmp_path: Path) -> None:
    cfg = Config(path=tmp_path / "c.toml", default=None, profiles={})
    with pytest.raises(ConfigError, match="not found"):
        cfg.resolve("nope")


def test_resolve_no_default_raises(tmp_path: Path) -> None:
    cfg = Config(path=tmp_path / "c.toml", default=None, profiles={})
    with pytest.raises(ConfigError, match="no default"):
        cfg.resolve(None)


def test_remove_clears_default_when_removing_it(tmp_path: Path) -> None:
    cfg = Config(path=tmp_path / "c.toml", default=None, profiles={})
    cfg.upsert(Profile(name="a", url="http://x", api_key="k"))
    cfg.upsert(Profile(name="b", url="http://y", api_key="k"))
    cfg.remove("a")  # was default; b should take over
    assert cfg.default == "b"


def test_remove_returns_the_removed_profile(tmp_path: Path) -> None:
    cfg = Config(path=tmp_path / "c.toml", default=None, profiles={})
    cfg.upsert(Profile(name="a", url="http://x", api_key="k"))
    removed = cfg.remove("a")
    assert removed.name == "a"
    assert cfg.default is None


def test_load_rejects_malformed_profile(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text('[profile.a]\nurl = "http://x"\n')  # no api_key
    with pytest.raises(ConfigError, match="missing"):
        Config.load(path)
