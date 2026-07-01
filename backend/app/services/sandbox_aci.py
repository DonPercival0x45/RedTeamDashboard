"""Azure Container Instances sandbox runner (v0.12.0).

Provisions a fresh ACI per tool invocation using the backend Container
App's managed identity. Source lands on an Azure Files share mounted
into the ACI at ``/tool``; the ACI runs, we poll for terminal state,
harvest logs, and delete the ACI.

Uses lazy imports of the Azure SDK so the module can be imported in
local dev where these packages aren't necessary.

Prereqs (provisioned by Bicep in ``infra/azure-kit`` starting with
v0.12.0):

- Storage account with a Files share (default name ``tool-sources``).
- ``azure_storage_account_name`` env set on the backend.
- Backend managed identity has:
  - ``Storage File Data SMB Share Contributor`` on the share
  - ``Contributor`` or ``Container Instance Contributor`` on the RG
- ``aci_subscription_id`` + ``aci_resource_group`` env set on the
  backend.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.services.sandbox_runner import (
    STDERR_CAP_BYTES,
    STDOUT_CAP_BYTES,
    SandboxRequest,
    SandboxRunner,
    SandboxRunResult,
    build_args_env,
    cap_output,
)

if TYPE_CHECKING:  # keep the SDK out of the import graph for local dev
    from azure.mgmt.containerinstance.models import ContainerGroup  # noqa: F401


_PYTHON_IMAGE = "python:3.12-slim"
_UBUNTU_IMAGE = "ubuntu:22.04"


class ACIRunnerNotConfigured(RuntimeError):
    """Raised when ``RTD_SANDBOX_RUNNER=aci`` is set but the required
    ACI settings (subscription id, resource group, storage account) are
    empty. Surface path: 500 with a message pointing at Bicep."""


class ACIRunner(SandboxRunner):
    name = "aci"

    def __init__(self) -> None:
        if (
            not settings.aci_subscription_id
            or not settings.aci_resource_group
            or not settings.azure_storage_account_name
        ):
            raise ACIRunnerNotConfigured(
                "ACIRunner requires aci_subscription_id, aci_resource_group, "
                "and azure_storage_account_name — set them in Bicep and "
                "the Container App env."
            )

    async def run(self, req: SandboxRequest) -> SandboxRunResult:
        started = time.monotonic()
        share_dir: str | None = None
        aci_name: str | None = None
        try:
            share_dir = await asyncio.to_thread(_upload_source_to_share, req)
            aci_name = _aci_name(req)
            await asyncio.to_thread(_create_aci, req, aci_name, share_dir)

            timed_out = await asyncio.to_thread(
                _wait_for_terminal, aci_name, req.timeout_seconds + 30
            )
            stdout_text, stderr_text, exit_code = await asyncio.to_thread(
                _harvest_result, aci_name
            )
            duration = time.monotonic() - started
            return SandboxRunResult(
                exit_code=exit_code if not timed_out else 124,
                stdout=cap_output(stdout_text, STDOUT_CAP_BYTES),
                stderr=cap_output(stderr_text, STDERR_CAP_BYTES),
                duration_seconds=duration,
                timed_out=timed_out,
                runtime_ref=aci_name,
            )
        except Exception as exc:  # noqa: BLE001 — surface infra errors on the row
            return SandboxRunResult(
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=time.monotonic() - started,
                timed_out=False,
                runtime_ref=aci_name,
                error=f"ACI runner: {exc}",
            )
        finally:
            if aci_name is not None:
                await asyncio.to_thread(_delete_aci_quiet, aci_name)
            if share_dir is not None:
                await asyncio.to_thread(_delete_share_dir_quiet, share_dir)


# ---------------------------------------------------------------------------
# Internals — Azure SDK plumbing kept out of the class body so the
# non-happy-path exception surface stays tight.
# ---------------------------------------------------------------------------


def _aci_name(req: SandboxRequest) -> str:
    """ACI resource names must be lowercase alnum + hyphen, 5-63 chars.
    Encode enough of the invocation id to be unique without leaking the
    full UUID (which would let anyone with access to the RG enumerate
    other invocations)."""
    slug = re.sub(r"[^a-z0-9-]", "-", req.tool_name.lower())[:20].strip("-")
    return f"rtd-tool-{slug}-{uuid.uuid4().hex[:12]}"


def _upload_source_to_share(req: SandboxRequest) -> str | None:
    """Write source to ``tools/<invocation-id>/`` on the Files share and
    return the subdirectory name so the caller can mount it. Returns
    None for binary kind."""
    if req.tool_kind == "binary":
        return None
    if req.source_bytes is None:
        raise ValueError(f"tool_kind={req.tool_kind} requires source bytes")
    from azure.identity import DefaultAzureCredential
    from azure.storage.fileshare import ShareServiceClient

    share_dir = f"tools/{req.invocation_id or uuid.uuid4().hex}"
    credential = DefaultAzureCredential()
    account_url = f"https://{settings.azure_storage_account_name}.file.core.windows.net"
    svc = ShareServiceClient(account_url=account_url, credential=credential)
    share = svc.get_share_client(settings.aci_source_share)
    # get_directory_client / create_directory idempotently
    _ensure_share_dir(share, share_dir)
    file_client = share.get_file_client(f"{share_dir}/{req.entrypoint}")
    file_client.upload_file(req.source_bytes)
    return share_dir


def _ensure_share_dir(share: Any, path: str) -> None:
    import contextlib

    parts = path.split("/")
    for i in range(1, len(parts) + 1):
        d = share.get_directory_client("/".join(parts[:i]))
        with contextlib.suppress(Exception):
            d.create_directory()


def _create_aci(req: SandboxRequest, aci_name: str, share_dir: str | None) -> None:
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.containerinstance import ContainerInstanceManagementClient
    from azure.mgmt.containerinstance.models import (
        AzureFileVolume,
        Container,
        ContainerGroup,
        ContainerGroupNetworkProtocol,  # noqa: F401
        EnvironmentVariable,
        OperatingSystemTypes,
        ResourceRequests,
        ResourceRequirements,
        Volume,
        VolumeMount,
    )

    credential = DefaultAzureCredential()
    client = ContainerInstanceManagementClient(
        credential, settings.aci_subscription_id
    )

    if req.tool_kind == "python":
        image = _PYTHON_IMAGE
        pip = _pip_bootstrap(req.python_deps)
        cmd = ["sh", "-c", f"{pip}exec python /tool/{req.entrypoint}"]
    elif req.tool_kind == "shell":
        image = _UBUNTU_IMAGE
        cmd = ["sh", f"/tool/{req.entrypoint}"]
    elif req.tool_kind == "binary":
        image = req.entrypoint
        cmd = None
    else:
        raise ValueError(f"unknown tool_kind: {req.tool_kind}")

    volumes = []
    volume_mounts = []
    if share_dir is not None:
        volumes.append(
            Volume(
                name="tool-src",
                azure_file=AzureFileVolume(
                    share_name=f"{settings.aci_source_share}/{share_dir}",
                    storage_account_name=settings.azure_storage_account_name,
                    # Managed-identity mount — no key needed. Requires
                    # the ``Storage File Data SMB Share Contributor``
                    # role assignment provisioned by Bicep.
                ),
            )
        )
        volume_mounts.append(
            VolumeMount(name="tool-src", mount_path="/tool", read_only=True)
        )

    container = Container(
        name="tool",
        image=image,
        command=cmd,
        resources=ResourceRequirements(
            requests=ResourceRequests(
                cpu=req.cpu_limit, memory_in_gb=req.memory_limit_mb / 1024.0
            )
        ),
        environment_variables=[
            EnvironmentVariable(name="RTD_ARGS_JSON", value=build_args_env(req))
        ],
        volume_mounts=volume_mounts or None,
    )

    group = ContainerGroup(
        location=settings.aci_location,
        containers=[container],
        os_type=OperatingSystemTypes.linux,
        restart_policy="Never",
        volumes=volumes or None,
    )

    poller = client.container_groups.begin_create_or_update(
        settings.aci_resource_group, aci_name, group
    )
    poller.result(timeout=180)  # LRO for provisioning; distinct from tool timeout


def _wait_for_terminal(aci_name: str, timeout_seconds: int) -> bool:
    """Poll the ACI until the tool container exits or we hit the
    outer timeout. Returns ``timed_out``."""
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.containerinstance import ContainerInstanceManagementClient

    credential = DefaultAzureCredential()
    client = ContainerInstanceManagementClient(
        credential, settings.aci_subscription_id
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        cg = client.container_groups.get(
            settings.aci_resource_group, aci_name
        )
        container = cg.containers[0] if cg.containers else None
        state = getattr(getattr(container, "instance_view", None), "current_state", None)
        state_name = getattr(state, "state", "")
        if state_name in ("Terminated",):
            return False
        time.sleep(3)
    return True


def _harvest_result(aci_name: str) -> tuple[str, str, int | None]:
    """Read the container logs + exit code from the ACI."""
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.containerinstance import ContainerInstanceManagementClient

    credential = DefaultAzureCredential()
    client = ContainerInstanceManagementClient(
        credential, settings.aci_subscription_id
    )
    cg = client.container_groups.get(settings.aci_resource_group, aci_name)
    logs = client.containers.list_logs(
        settings.aci_resource_group, aci_name, "tool"
    )
    stdout_text = getattr(logs, "content", "") or ""
    # ACI merges stdout+stderr into a single log stream. Split heuristics
    # aren't robust; treat everything as stdout and leave stderr blank.
    container = cg.containers[0] if cg.containers else None
    state = getattr(getattr(container, "instance_view", None), "current_state", None)
    exit_code = getattr(state, "exit_code", None)
    return stdout_text, "", exit_code


def _delete_aci_quiet(aci_name: str) -> None:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.containerinstance import ContainerInstanceManagementClient

        credential = DefaultAzureCredential()
        client = ContainerInstanceManagementClient(
            credential, settings.aci_subscription_id
        )
        client.container_groups.begin_delete(
            settings.aci_resource_group, aci_name
        )
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass


def _delete_share_dir_quiet(share_dir: str) -> None:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.fileshare import ShareServiceClient

        credential = DefaultAzureCredential()
        svc = ShareServiceClient(
            account_url=(
                f"https://{settings.azure_storage_account_name}.file.core.windows.net"
            ),
            credential=credential,
        )
        share = svc.get_share_client(settings.aci_source_share)
        directory = share.get_directory_client(share_dir)
        for f in directory.list_directories_and_files():
            if not f["is_directory"]:
                directory.get_file_client(f["name"]).delete_file()
        directory.delete_directory()
    except Exception:  # noqa: BLE001
        pass


def _pip_bootstrap(deps: list[str]) -> str:
    if not deps:
        return ""
    return (
        "pip install --quiet --no-input --disable-pip-version-check "
        + " ".join(f"'{d}'" for d in deps)
        + " >&2 && "
    )
