"""Sandbox runner abstraction (v0.12.0).

Every tool invocation runs in a fresh sibling container. Two concrete
runners implement the same protocol:

- ``LocalDockerRunner`` (:mod:`app.services.sandbox_local`) — mounts
  ``/var/run/docker.sock`` and spawns a container per invocation. Used
  in local dev + CI.

- ``ACIRunner`` (:mod:`app.services.sandbox_aci`) — provisions an Azure
  Container Instance per invocation via the backend's managed identity.
  Used in prod (5qprod). Source arrives via an Azure Files share
  mounted into the ACI.

The service layer picks a runner via ``settings.sandbox_runner`` (env
var ``RTD_SANDBOX_RUNNER``): ``docker`` in dev, ``aci`` in prod.
Callers do not know or care which one they got.

Shared invariants enforced HERE (not per-runner):

- Args JSON is delivered as a base64-encoded env var ``RTD_ARGS_JSON``.
  Tools decode it with::

      import base64, json, os
      payload = json.loads(base64.b64decode(os.environ["RTD_ARGS_JSON"]))
      args = payload["args"]
      scope = payload["scope"]
      entities = payload.get("entities", {})

  Env var chosen over stdin so both runners have a uniform contract
  (ACI does not expose per-invocation stdin via the mgmt API).

  Payload shape::

      {"args": {...}, "scope": {...}, "entities": {...},
       "invocation_id": "...", "tool_name": "...", "tool_version": N}

  Tools do their work, print results to stdout, exit 0. Errors go to
  stderr, non-zero exit.

- Stdout / stderr are size-capped at 10 MB each. Larger tools get
  truncated with a warning marker appended so ``exit_code`` still
  reflects what the tool returned.

- Timeout is enforced by the runner (SIGKILL). ``timed_out=True`` on
  the result; ``exit_code`` is typically 124 (docker) or None (ACI).
"""
from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

STDOUT_CAP_BYTES = 10 * 1024 * 1024  # 10 MB
STDERR_CAP_BYTES = 10 * 1024 * 1024
_TRUNC_MARKER = "\n\n[rtd: output truncated at 10 MB]\n"

# Per-runner USD-per-second estimate stamped on each invocation row so
# the Costs tab can roll up tool spend alongside LLM spend. Rough
# region-average numbers; refined pricing lookup slips to v0.17.
#
# LocalDockerRunner: $0 — free host compute.
# ACIRunner: ~1 vCPU + 512 MiB + LB overhead ≈ $0.044/hour ≈ $1.22e-5/s
#   at centralus. Round up to $2e-5 to cover pull time + storage +
#   the fact that we're paying for the whole allocation window even if
#   the tool exits early. Good-enough spend signal, NOT a billing
#   source of truth.
RUNTIME_RATES_USD_PER_SECOND: dict[str, float] = {
    "docker": 0.0,
    "aci": 2e-5,
}


@dataclass
class SandboxRequest:
    """Input to a runner. Callers build one of these and hand it to
    :meth:`SandboxRunner.run`."""

    tool_id: str
    tool_name: str
    tool_version: int
    tool_kind: str  # "python" | "shell" | "binary"
    entrypoint: str  # relative path inside /tool for python/shell; image tag for binary
    source_bytes: bytes | None  # None for binary kind
    python_deps: list[str] = field(default_factory=list)
    args: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    # v0.16.0: discovered entities (emails / hosts / IPs extracted from
    # findings) grouped by type. Parallel to ``scope`` (which is the
    # engagement's *defined* targets) so a tool can tell apart "what's
    # authorized" from "what we've found". Delivered to the entrypoint
    # under payload["entities"].
    entities: dict[str, Any] = field(default_factory=dict)
    invocation_id: str = ""
    timeout_seconds: int = 120
    cpu_limit: float = 1.0
    memory_limit_mb: int = 512
    # ``none`` = no network; any other value = network allowed. v0.12
    # keeps the policy binary; v0.15 tightens to per-token egress.
    allow_network: bool = False


@dataclass
class SandboxRunResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    runtime_ref: str | None = None  # container id / ACI resource id
    error: str | None = None


class SandboxRunner(ABC):
    """Contract every concrete runner implements."""

    name: str = "abstract"

    @abstractmethod
    async def run(self, req: SandboxRequest) -> SandboxRunResult:
        """Execute the tool in a fresh sandbox and return the captured
        outputs. Must NEVER raise for tool-runtime failures — those are
        surfaced via ``exit_code`` / ``error`` on the result. Only raise
        for infra failures (docker daemon down, Azure API unreachable)."""


def build_args_env(req: SandboxRequest) -> str:
    """Serialise the args-payload into the base64 blob every runner
    passes as ``RTD_ARGS_JSON`` env var. Kept here so both runners
    produce byte-identical values for the same request — useful when
    debugging a tool that behaves differently between local and prod."""
    payload = json.dumps(
        {
            "args": req.args,
            "scope": req.scope,
            "entities": req.entities,
            "invocation_id": req.invocation_id,
            "tool_name": req.tool_name,
            "tool_version": req.tool_version,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def cap_output(text: str, cap: int = STDOUT_CAP_BYTES) -> str:
    """Truncate an output string to ``cap`` bytes and append a marker if
    truncation actually happened. Encoded-length aware so multi-byte
    characters don't split at cap."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= cap:
        return text
    return encoded[:cap].decode("utf-8", errors="ignore") + _TRUNC_MARKER
