"""Local-Docker sandbox runner (v0.12.0).

Spawns a fresh container per tool invocation via the docker CLI. The
backend container mounts ``/var/run/docker.sock`` in local dev
(``infra/docker-compose.yml``) so this runner can talk to the host
daemon. Not used in prod — that's ``ACIRunner``.

Container contract:

- Base image: ``python:3.12-slim`` (Python + shell). For binary tools
  (v0.14+), ``req.entrypoint`` is used as the image directly.
- Source (Python/shell) lands in an ephemeral tmp dir on the host and
  is bind-mounted read-only at ``/tool``.
- Args JSON is streamed on stdin.
- ``req.allow_network=False`` → ``--network none``; else default bridge
  (permissive). v0.15 tightens to per-egress-token policy.
- CPU + memory caps applied via ``--cpus`` / ``--memory``.
- Timeout via ``--stop-timeout`` + wall-clock kill.

Not thread-safe with itself in the sense that each invocation writes to
a unique tmp dir; the whole class is stateless otherwise.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from app.services.sandbox_runner import (
    STDERR_CAP_BYTES,
    STDOUT_CAP_BYTES,
    SandboxRequest,
    SandboxRunner,
    SandboxRunResult,
    build_args_env,
    cap_output,
)

_DEFAULT_PYTHON_IMAGE = "python:3.12-slim"

# Path visible on BOTH the backend container and the host docker
# daemon. Bind-mounted in ``infra/docker-compose.yml``. When we do
# ``docker run -v <this>/foo:/tool``, the daemon on the host resolves
# the path in the host filesystem — which only works if that same
# path exists there. Docker-outside-of-docker plumbing.
_SOURCE_SHARE = Path(os.getenv("RTD_TOOL_SOURCE_SHARE", "/tmp/rtd-tool-sources"))


class LocalDockerRunner(SandboxRunner):
    name = "docker"

    async def run(self, req: SandboxRequest) -> SandboxRunResult:
        started = time.monotonic()

        source_dir: Path | None = None
        try:
            source_dir = _materialise_source(req)
            command = _build_command(req, source_dir)

            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            timed_out = False
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    # Add a small buffer so the container-side timeout
                    # (enforced by docker's --stop-timeout after SIGKILL)
                    # fires before this outer wait_for does. Both are
                    # backstops for each other.
                    timeout=req.timeout_seconds + 5,
                )
            except TimeoutError:
                timed_out = True
                proc.kill()
                # Give docker a beat to reap before we harvest whatever
                # partial output exists.
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(), timeout=5
                    )
                except TimeoutError:
                    stdout_bytes, stderr_bytes = b"", b""

            duration = time.monotonic() - started
            return SandboxRunResult(
                exit_code=proc.returncode if not timed_out else 124,
                stdout=cap_output(
                    stdout_bytes.decode("utf-8", errors="replace"),
                    STDOUT_CAP_BYTES,
                ),
                stderr=cap_output(
                    stderr_bytes.decode("utf-8", errors="replace"),
                    STDERR_CAP_BYTES,
                ),
                duration_seconds=duration,
                timed_out=timed_out,
                runtime_ref=None,
            )
        except FileNotFoundError as exc:
            # docker binary not present in the backend container. Local
            # dev needs the docker.sock mount + docker CLI installed in
            # the image; misconfigs land here.
            return SandboxRunResult(
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=time.monotonic() - started,
                timed_out=False,
                error=f"docker CLI not available in backend container: {exc}",
            )
        finally:
            if source_dir is not None and source_dir.exists():
                shutil.rmtree(source_dir, ignore_errors=True)


def _materialise_source(req: SandboxRequest) -> Path | None:
    """Write the tool's source bytes to a directory that both the
    backend container AND the host docker daemon can see. Returns
    None for binary kind (no source; entrypoint is the image tag).

    Uses :data:`_SOURCE_SHARE` (bind-mounted, same path on both sides)
    when present; falls back to :func:`tempfile.mkdtemp` only for
    non-DooD environments (e.g., when the backend is running natively
    on the host, not in a container)."""
    if req.tool_kind == "binary":
        return None
    if req.source_bytes is None:
        raise ValueError(f"tool_kind={req.tool_kind} requires source bytes")
    if _SOURCE_SHARE.exists():
        d = _SOURCE_SHARE / f"{req.tool_id[:8]}-{uuid.uuid4().hex[:8]}"
        d.mkdir(parents=True, exist_ok=True)
    else:
        d = Path(tempfile.mkdtemp(prefix=f"rtd-tool-{req.tool_id[:8]}-"))
    (d / req.entrypoint).write_bytes(req.source_bytes)
    return d


def _build_command(req: SandboxRequest, source_dir: Path | None) -> list[str]:
    """Assemble the ``docker run`` argv. Kept as a pure function so
    tests can assert the exact command shape."""
    cmd: list[str] = [
        "docker", "run",
        "--rm",
        "--cpus", str(req.cpu_limit),
        "--memory", f"{req.memory_limit_mb}m",
        "--stop-timeout", str(req.timeout_seconds),
        "-e", f"RTD_ARGS_JSON={build_args_env(req)}",
    ]
    if not req.allow_network:
        cmd += ["--network", "none"]
    if source_dir is not None:
        cmd += ["-v", f"{source_dir}:/tool:ro"]
        cmd += ["-w", "/tool"]

    if req.tool_kind == "python":
        image = _DEFAULT_PYTHON_IMAGE
        # Install manifest-declared deps at container start. Runs quiet
        # to keep the log noise off stderr; failures land in stderr and
        # exit-nonzero, which the invocation row surfaces to the admin.
        entrypoint = f"/tool/{req.entrypoint}"
        pip = _pip_bootstrap(req.python_deps)
        cmd += [image, "sh", "-c", f"{pip}exec python {entrypoint}"]
    elif req.tool_kind == "shell":
        image = "ubuntu:22.04"
        entrypoint = f"/tool/{req.entrypoint}"
        cmd += [image, "sh", entrypoint]
    elif req.tool_kind == "binary":
        cmd += [req.entrypoint]
    else:
        raise ValueError(f"unknown tool_kind: {req.tool_kind}")

    return cmd


def _pip_bootstrap(deps: list[str]) -> str:
    """Return a shell prefix that pip-installs manifest-declared deps
    before the tool runs. Empty string if no deps. Only vetted names
    that already passed the AST allow-list should ever land here."""
    if not deps:
        return ""
    # ``--quiet`` suppresses normal output; only errors bubble.
    # ``--no-input`` prevents any interactive prompts from hanging.
    return (
        "pip install --quiet --no-input --disable-pip-version-check "
        + " ".join(f"'{d}'" for d in deps)
        + " >&2 && "
    )
