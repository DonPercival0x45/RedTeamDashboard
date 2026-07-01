"""Tools manifest parsing + static validation (v0.11.0).

Every uploaded tool declares a YAML manifest of the form::

    apiVersion: rtd.tools/v1
    kind: Tool
    metadata:
      name: subdomain-fuzz
      description: DNS brute-force from a wordlist
    spec:
      kind: python              # python | shell | binary
      lane: analyst             # analyst | admin
      entrypoint: main.py       # or OCI image tag for binary
      args:
        - name: target
          type: string
          required: true
          scope_kind: domain    # optional; scope filter for the arg value
        - name: wordlist
          type: enum
          values: [small, medium, large]
      timeout_seconds: 300
      risk_level: passive       # passive | active | destructive
      network_egress: [dns]     # default deny-all
      task_kind: enum           # enum | scan | exploit (charter gate)

Layer 2 of the analyst-lane gate is here: schema validation + a set of
sanity rules that catch obvious footguns (e.g. ``risk=passive`` with
``network_egress: [all]``, ``lane=analyst`` with ``kind=binary``,
``task_kind=exploit`` with ``lane=analyst`` — allowed but must set
``risk_level=destructive``).

Layer 1 (AST allow-list) lives in ``tool_ast_check``; Layer 3 (LLM
review) lands in v0.13.0; Layer 4 (admin approval) lives at the API
layer.
"""
from __future__ import annotations

from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.models import ToolKind, ToolLane, ToolTaskKind

_MANIFEST_API_VERSION = "rtd.tools/v1"
_MANIFEST_KIND = "Tool"

_VALID_RISK_LEVELS = frozenset({"passive", "active", "destructive"})
_VALID_ARG_TYPES = frozenset({"string", "integer", "boolean", "enum"})
_VALID_EGRESS_TOKENS = frozenset(
    {"none", "dns", "http", "https", "custom", "all"}
)


class ToolArgSpec(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    type: Literal["string", "integer", "boolean", "enum"] = "string"
    required: bool = False
    scope_kind: str | None = None  # 'domain' | 'ip' | 'cidr' | 'url' | None
    values: list[str] = Field(default_factory=list)
    description: str | None = None

    @field_validator("values")
    @classmethod
    def _values_only_for_enum(cls, v: list[str]) -> list[str]:
        return v  # cross-field check happens in the parent validator


class ToolSpec(BaseModel):
    kind: ToolKind
    lane: ToolLane
    entrypoint: str = Field(min_length=1, max_length=200)
    args: list[ToolArgSpec] = Field(default_factory=list)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    risk_level: str = "passive"
    network_egress: list[str] = Field(default_factory=lambda: ["none"])
    task_kind: ToolTaskKind = ToolTaskKind.enum

    @field_validator("risk_level")
    @classmethod
    def _valid_risk(cls, v: str) -> str:
        if v not in _VALID_RISK_LEVELS:
            raise ValueError(
                f"risk_level must be one of {sorted(_VALID_RISK_LEVELS)}"
            )
        return v

    @field_validator("network_egress")
    @classmethod
    def _valid_egress(cls, v: list[str]) -> list[str]:
        bad = [t for t in v if t not in _VALID_EGRESS_TOKENS]
        if bad:
            raise ValueError(
                f"network_egress tokens {bad} not in "
                f"{sorted(_VALID_EGRESS_TOKENS)}"
            )
        return v


class ToolMetadata(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9-]*$")
    description: str | None = Field(default=None, max_length=500)


class ToolManifest(BaseModel):
    """Parsed + typed manifest. See module docstring for the YAML shape."""

    apiVersion: str
    kind: str
    metadata: ToolMetadata
    spec: ToolSpec

    @field_validator("apiVersion")
    @classmethod
    def _valid_api_version(cls, v: str) -> str:
        if v != _MANIFEST_API_VERSION:
            raise ValueError(
                f"apiVersion must be '{_MANIFEST_API_VERSION}', got '{v}'"
            )
        return v

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, v: str) -> str:
        if v != _MANIFEST_KIND:
            raise ValueError(f"kind must be '{_MANIFEST_KIND}', got '{v}'")
        return v


class ManifestParseError(Exception):
    """Raised when a manifest YAML string can't be parsed or fails the
    schema/sanity checks. The ``errors`` attribute is a flat list of
    dot-path + message pairs suitable for surfacing in the upload UI."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors) or "manifest invalid")
        self.errors = errors


def parse_manifest(raw: str) -> ToolManifest:
    """Parse YAML text → typed ``ToolManifest`` and run cross-field sanity
    checks. Raises :class:`ManifestParseError` with a flat error list on
    the first pass that fails; the caller does not need to re-loop."""
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ManifestParseError([f"YAML parse: {exc}"]) from exc
    if not isinstance(loaded, dict):
        raise ManifestParseError(["manifest must be a YAML mapping at the root"])

    try:
        manifest = ToolManifest.model_validate(loaded)
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        ]
        raise ManifestParseError(errors) from exc

    cross_errors = _cross_field_checks(manifest)
    if cross_errors:
        raise ManifestParseError(cross_errors)
    return manifest


def _cross_field_checks(m: ToolManifest) -> list[str]:
    errors: list[str] = []
    spec = m.spec

    # arg-level cross checks (enum requires values, etc.)
    for i, arg in enumerate(spec.args):
        prefix = f"spec.args[{i}].{arg.name}"
        if arg.type == "enum" and not arg.values:
            errors.append(f"{prefix}: enum type requires a non-empty 'values' list")
        if arg.type != "enum" and arg.values:
            errors.append(
                f"{prefix}: 'values' is only valid when type=enum"
            )

    # binary tools cannot ride the analyst lane — LLM can't audit a
    # compiled artifact. This is the load-bearing charter contract for
    # the two-lane trust model.
    if spec.kind == ToolKind.binary and spec.lane == ToolLane.analyst:
        errors.append(
            "spec: binary kind requires lane=admin (analyst lane cannot "
            "carry compiled artifacts)"
        )

    # analyst-lane exploit tools must escalate risk to 'destructive' —
    # signals the reviewing admin that this is not a routine approval.
    if (
        spec.task_kind == ToolTaskKind.exploit
        and spec.lane == ToolLane.analyst
        and spec.risk_level != "destructive"
    ):
        errors.append(
            "spec: task_kind=exploit on analyst lane requires "
            "risk_level=destructive"
        )

    # passive/active tools should not declare wide-open egress. If
    # risk_level==passive, only 'none' or 'dns' egress makes sense.
    if spec.risk_level == "passive" and any(
        t in spec.network_egress for t in ("http", "https", "custom", "all")
    ):
        errors.append(
            "spec: risk_level=passive cannot declare http/https/custom/all "
            "in network_egress"
        )
    if "all" in spec.network_egress and spec.risk_level != "destructive":
        errors.append(
            "spec: network_egress=[all] is reserved for risk_level=destructive"
        )

    # entrypoint sanity by kind
    if spec.kind == ToolKind.python and not spec.entrypoint.endswith(".py"):
        errors.append("spec.entrypoint: python kind requires a .py file name")
    if spec.kind == ToolKind.shell and not (
        spec.entrypoint.endswith(".sh") or spec.entrypoint.endswith(".bash")
    ):
        errors.append("spec.entrypoint: shell kind requires .sh or .bash")
    if spec.kind == ToolKind.binary and "/" not in spec.entrypoint:
        errors.append(
            "spec.entrypoint: binary kind requires an OCI image tag "
            "(e.g. 'ghcr.io/rtd/nmap:7.94')"
        )

    return errors


def manifest_to_jsonb(m: ToolManifest) -> dict[str, Any]:
    """Serialise a validated manifest to JSON-safe dict for the DB
    ``manifest`` column. Pydantic already knows how to render StrEnum
    values, so ``model_dump(mode='json')`` is all we need."""
    return m.model_dump(mode="json")
