"""OCI image reference parsing (v0.14.0).

Binary-lane tools declare their entrypoint as an OCI image reference
(``ghcr.io/rtd/nmap:7.94`` or ``ghcr.io/rtd/nmap@sha256:…``). This
module parses + validates that reference before we hand it to the
sandbox runner — we do not want an admin to accidentally register
``ghcr.io/rtd/nmap`` (no tag → defaults to :latest, which floats and
makes reproducibility impossible), or ``some junk`` (blows up in the
runner with a cryptic docker error).

Distinguishes tag-based references (mutable) from digest-based ones
(immutable) so the admin UI can render a warning next to any tool
without a pinned digest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Loosely modeled on the OCI distribution spec's reference grammar.
# Simplified: registry is a hostname optionally followed by :port, then
# a path of one-or-more ``/``-separated components, then :tag or
# @sha256:hex.
_HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::\d+)?$")
_PATH_COMPONENT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
_TAG_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


@dataclass
class ImageRef:
    raw: str
    registry: str | None  # None = docker hub (implicit index.docker.io)
    repository: str
    tag: str | None
    digest: str | None
    is_pinned: bool  # True iff digest is set

    def to_json(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "registry": self.registry,
            "repository": self.repository,
            "tag": self.tag,
            "digest": self.digest,
            "is_pinned": self.is_pinned,
        }


class ImageRefError(ValueError):
    """Raised when a string doesn't parse as a valid OCI image reference."""


def parse_image_ref(raw: str) -> ImageRef:
    """Return a structured :class:`ImageRef` or raise
    :class:`ImageRefError`. Enforces:

    - A tag or digest MUST be present (no implicit :latest — pins are
      the whole point of registering the image at approval time).
    - Registry (if any) is a valid hostname[:port].
    - Repository path components match the OCI grammar.
    """
    if not raw or not raw.strip():
        raise ImageRefError("image reference is empty")
    ref = raw.strip()

    # digest form first — @sha256:… trumps :tag if both were somehow present
    digest: str | None = None
    if "@" in ref:
        ref, digest = ref.rsplit("@", 1)
        if not _DIGEST_RE.match(digest):
            raise ImageRefError(
                f"digest '{digest}' must match sha256:<64 hex chars>"
            )

    # tag — after the LAST colon that's part of the path, not the registry
    tag: str | None = None
    if ":" in ref:
        # Distinguish registry ':port' from ':tag'. The tag comes after
        # the last path component, which comes after the last ``/``. If
        # the last ``:`` sits after the last ``/``, it's the tag.
        last_slash = ref.rfind("/")
        last_colon = ref.rfind(":")
        if last_colon > last_slash:
            ref, tag = ref[:last_colon], ref[last_colon + 1 :]
            if not _TAG_RE.match(tag):
                raise ImageRefError(
                    f"tag '{tag}' has invalid characters (a-zA-Z0-9._-, "
                    "≤128 chars, must start with a-zA-Z0-9_)"
                )

    if not tag and not digest:
        raise ImageRefError(
            "image reference must include a :tag or @sha256:… digest — "
            "unpinned :latest is not allowed"
        )

    # Split registry vs repository. Docker's rule of thumb: if the first
    # path component contains a '.' or ':', or is 'localhost', treat it
    # as a registry hostname. Otherwise it's part of the repository on
    # docker hub.
    if "/" in ref:
        first, rest = ref.split("/", 1)
        looks_like_registry = (
            "." in first or ":" in first or first == "localhost"
        )
        if looks_like_registry:
            registry: str | None = first
            repository = rest
        else:
            registry = None
            repository = ref
    else:
        registry = None
        repository = ref

    if registry is not None and not _HOSTNAME_RE.match(registry):
        raise ImageRefError(
            f"registry '{registry}' is not a valid hostname[:port]"
        )
    for component in repository.split("/"):
        if not _PATH_COMPONENT_RE.match(component):
            raise ImageRefError(
                f"repository component '{component}' is invalid — must be "
                "lowercase alphanumeric with . _ - separators"
            )

    return ImageRef(
        raw=raw.strip(),
        registry=registry,
        repository=repository,
        tag=tag,
        digest=digest,
        is_pinned=digest is not None,
    )
