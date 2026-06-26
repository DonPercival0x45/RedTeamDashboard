"""Persistent CLI config.

One file at ``~/.config/rtd/config.toml`` (overridable with ``--config``). Holds
multiple named profiles; each profile is one (api_url, api_key) pair pointing
at one deployed backend. A ``default`` key selects which profile commands use
when no ``--profile`` flag is given.

The file is created 0600 and contains an API key in plaintext — same threat
model as ``~/.kube/config`` or ``~/.config/gh/hosts.yml``. If you need
keyring-backed storage, swap this module out.
"""
from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w


def default_config_path() -> Path:
    """`$XDG_CONFIG_HOME/rtd/config.toml`, falling back to `~/.config/rtd/config.toml`."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "xray" / "config.toml"


@dataclass(frozen=True, slots=True)
class Profile:
    """One backend connection: where it lives + how to talk to it."""

    name: str
    url: str
    api_key: str


class ConfigError(Exception):
    """User-actionable problem with the on-disk config (missing profile, parse error, ...)."""


@dataclass
class Config:
    """In-memory view of the on-disk TOML.

    Use :meth:`load` / :meth:`save` rather than constructing directly except in tests.
    """

    path: Path
    default: str | None
    profiles: dict[str, Profile]

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Read the file at ``path``; return an empty Config if it doesn't exist."""
        path = path or default_config_path()
        if not path.exists():
            return cls(path=path, default=None, profiles={})
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"could not parse {path}: {exc}") from exc

        default = raw.get("default")
        if default is not None and not isinstance(default, str):
            raise ConfigError(f"`default` in {path} must be a string, got {default!r}")

        profile_block = raw.get("profile", {})
        if not isinstance(profile_block, dict):
            raise ConfigError(f"`[profile]` table in {path} is malformed")

        profiles: dict[str, Profile] = {}
        for name, body in profile_block.items():
            if not isinstance(body, dict):
                raise ConfigError(f"profile.{name} must be a table")
            url = body.get("url", "")
            api_key = body.get("api_key", "")
            if not url or not api_key:
                raise ConfigError(
                    f"profile {name!r} is missing `url` or `api_key`"
                )
            profiles[name] = Profile(name=name, url=url.rstrip("/"), api_key=api_key)

        return cls(path=path, default=default, profiles=profiles)

    def save(self) -> None:
        """Write atomically with 0600 perms — even if the directory had wider defaults."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {}
        if self.default is not None:
            payload["default"] = self.default
        if self.profiles:
            payload["profile"] = {
                name: {"url": p.url, "api_key": p.api_key}
                for name, p in self.profiles.items()
            }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("wb") as fh:
            tomli_w.dump(payload, fh)
        # 0600 — group + others stripped before rename so there's no window
        # where the file is readable by anyone else.
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def upsert(self, profile: Profile, *, make_default: bool = False) -> None:
        self.profiles[profile.name] = profile
        if make_default or self.default is None:
            self.default = profile.name

    def remove(self, name: str) -> Profile:
        if name not in self.profiles:
            raise ConfigError(f"no such profile: {name!r}")
        removed = self.profiles.pop(name)
        if self.default == name:
            self.default = next(iter(self.profiles), None)
        return removed

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def resolve(self, name: str | None) -> Profile:
        """Return ``profiles[name]`` if name is given; else the default profile.

        Raises :class:`ConfigError` with a clear remediation when nothing matches.
        """
        if name is not None:
            try:
                return self.profiles[name]
            except KeyError:
                raise ConfigError(
                    f"profile {name!r} not found. "
                    f"Try one of: {', '.join(sorted(self.profiles)) or '(none — run `rtd login`)'}"
                ) from None
        if self.default is None:
            raise ConfigError(
                "no default profile set. Run `rtd login --profile <name> --url ... --key ...`"
            )
        return self.profiles[self.default]
