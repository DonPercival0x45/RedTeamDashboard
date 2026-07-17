"""Thin wrapper over the Azure ARM SDK for the Infrastructure tab.

Every call is scoped to one subscription. ``list_all_vms`` fans out across
every configured subscription in parallel. Managed identity credentials are
picked up via ``DefaultAzureCredential`` (already vendored — same path
as blob and sandbox_aci).

Mock mode (``env=local`` + empty ``infra_subscriptions``) returns two
fixture VMs so the UI is exercisable without an Azure cred path. Mock and
real go through the same ``AzureArmService`` protocol so the router does
not care which is active.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import structlog
from fastapi.concurrency import run_in_threadpool

from app.core.config import Settings

log = structlog.get_logger(__name__)

PowerState = Literal[
    "running", "stopped", "deallocated", "starting", "stopping",
    "deallocating", "unknown",
]


@dataclass(slots=True)
class VmSummary:
    """Wire-shape returned by ``GET /infrastructure/vms``."""

    arm_id: str
    name: str
    subscription_id: str
    resource_group: str
    location: str
    size: str
    os_type: str
    os_offer: str | None
    power_state: PowerState
    public_ip: str | None
    private_ip: str | None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SubscriptionSummary:
    subscription_id: str
    display_name: str
    state: str


@dataclass(slots=True)
class RunCommandResult:
    """v2.12.0 — one-shot shell/PowerShell execution against a VM.

    Wraps Azure's synchronous ``Microsoft.Compute/.../runCommand`` LRO —
    the backend polls until the long-running-operation resolves, then
    parses the `value` array into stdout / stderr / exit_code buckets.
    Timeout surfaces as ``timed_out=True`` with whatever partial output
    Azure had emitted.
    """

    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool = False


@dataclass(slots=True)
class AutoShutdown:
    """v2.11.0 — mirrors the subset of Microsoft.DevTestLab/schedules we surface.

    The resource lives at
    ``{RG}/providers/Microsoft.DevTestLab/schedules/shutdown-computevm-{vmName}``
    and Azure's own auto-shutdown feature in the portal writes to this
    same resource. ``time_hhmm`` is a 4-digit local time ("1900" == 19:00);
    ``timezone_id`` is a Windows time-zone id (e.g. "Central Standard Time"),
    not IANA. ``notification_webhook_url`` empty → notifications disabled.
    """

    enabled: bool
    time_hhmm: str
    timezone_id: str
    notification_webhook_url: str | None = None
    notification_minutes: int = 30


class AzureArmService(Protocol):
    async def list_subscriptions(self) -> list[SubscriptionSummary]: ...
    async def list_all_vms(self) -> list[VmSummary]: ...
    async def get_vm(self, arm_id: str) -> VmSummary: ...
    async def start_vm(self, arm_id: str) -> None: ...
    async def deallocate_vm(self, arm_id: str) -> None: ...
    async def restart_vm(self, arm_id: str) -> None: ...
    async def get_auto_shutdown(self, arm_id: str) -> AutoShutdown | None: ...
    async def set_auto_shutdown(
        self, arm_id: str, schedule: AutoShutdown
    ) -> AutoShutdown: ...
    async def delete_auto_shutdown(self, arm_id: str) -> None: ...
    async def run_command(
        self, arm_id: str, script: str, os_type: str
    ) -> RunCommandResult: ...


# ---------------------------------------------------------------------------
# ARM id parsing — canonical shape is
# /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{name}
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VmRef:
    subscription_id: str
    resource_group: str
    name: str


def parse_vm_arm_id(arm_id: str) -> VmRef:
    parts = arm_id.strip("/").split("/")
    # Case-insensitive: Azure emits `resourceGroups` but browsers can
    # arrive with any casing. Normalize on parse; re-emit canonical.
    lowered = [p.lower() for p in parts]
    try:
        s = lowered.index("subscriptions")
        r = lowered.index("resourcegroups")
        v = lowered.index("virtualmachines")
    except ValueError as exc:
        raise ValueError(f"not a virtualMachines ARM id: {arm_id}") from exc
    return VmRef(
        subscription_id=parts[s + 1],
        resource_group=parts[r + 1],
        name=parts[v + 1],
    )


def format_vm_arm_id(ref: VmRef) -> str:
    return (
        f"/subscriptions/{ref.subscription_id}"
        f"/resourceGroups/{ref.resource_group}"
        f"/providers/Microsoft.Compute/virtualMachines/{ref.name}"
    )


# ---------------------------------------------------------------------------
# Real implementation — one Compute + Network client per sub, cached.
# ---------------------------------------------------------------------------


class RealAzureArmService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._compute: dict[str, object] = {}
        self._network: dict[str, object] = {}
        self._credential: object | None = None

    def _cred(self) -> object:
        if self._credential is None:
            from azure.identity import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
        return self._credential

    def _compute_client(self, subscription_id: str) -> object:
        client = self._compute.get(subscription_id)
        if client is None:
            from azure.mgmt.compute import ComputeManagementClient

            client = ComputeManagementClient(self._cred(), subscription_id)
            self._compute[subscription_id] = client
        return client

    def _network_client(self, subscription_id: str) -> object:
        client = self._network.get(subscription_id)
        if client is None:
            from azure.mgmt.network import NetworkManagementClient

            client = NetworkManagementClient(self._cred(), subscription_id)
            self._network[subscription_id] = client
        return client

    async def list_subscriptions(self) -> list[SubscriptionSummary]:
        # Only surface configured subs — we don't want to leak sub display
        # names the caller isn't authorized to act against.
        def _work() -> list[SubscriptionSummary]:
            from azure.mgmt.subscription import SubscriptionClient

            client = SubscriptionClient(self._cred())
            out: list[SubscriptionSummary] = []
            configured = set(self._settings.infra_subscriptions)
            for sub in client.subscriptions.list():
                if sub.subscription_id in configured:
                    out.append(
                        SubscriptionSummary(
                            subscription_id=sub.subscription_id,
                            display_name=sub.display_name or sub.subscription_id,
                            state=str(sub.state) if sub.state else "unknown",
                        )
                    )
            return out

        return await run_in_threadpool(_work)

    async def list_all_vms(self) -> list[VmSummary]:
        subs = self._settings.infra_subscriptions
        if not subs:
            return []
        results = await asyncio.gather(
            *(self._list_vms_for_sub(sub_id) for sub_id in subs),
            return_exceptions=True,
        )
        flat: list[VmSummary] = []
        for sub_id, r in zip(subs, results, strict=True):
            if isinstance(r, list):
                flat.extend(r)
            else:
                # v2.10.2: surface the real Azure error so an admin
                # debugging an empty list can see what's failing per-sub
                # (RBAC not propagated, wrong sub id, transient throttle,
                # etc.). Previously we swallowed silently.
                log.warning(
                    "infra_list_vms_failed",
                    subscription_id=sub_id,
                    error=repr(r),
                )
        return flat

    async def _list_vms_for_sub(self, subscription_id: str) -> list[VmSummary]:
        def _work() -> list[VmSummary]:
            return _collect_vms_for_sub(
                self._compute_client(subscription_id),
                self._network_client(subscription_id),
                subscription_id,
            )

        return await run_in_threadpool(_work)

    async def get_vm(self, arm_id: str) -> VmSummary:
        ref = parse_vm_arm_id(arm_id)

        def _work() -> VmSummary:
            compute = self._compute_client(ref.subscription_id)
            network = self._network_client(ref.subscription_id)
            return _hydrate_vm(
                compute,
                network,
                ref.subscription_id,
                ref.resource_group,
                ref.name,
                expand_instance_view=True,
            )

        return await run_in_threadpool(_work)

    async def start_vm(self, arm_id: str) -> None:
        ref = parse_vm_arm_id(arm_id)

        def _work() -> None:
            client = self._compute_client(ref.subscription_id)
            # ``.begin_start`` returns a long-running-operation poller. We
            # kick it and return — the frontend polls status via GET /vm.
            client.virtual_machines.begin_start(ref.resource_group, ref.name)

        await run_in_threadpool(_work)

    async def deallocate_vm(self, arm_id: str) -> None:
        ref = parse_vm_arm_id(arm_id)

        def _work() -> None:
            client = self._compute_client(ref.subscription_id)
            client.virtual_machines.begin_deallocate(ref.resource_group, ref.name)

        await run_in_threadpool(_work)

    async def restart_vm(self, arm_id: str) -> None:
        ref = parse_vm_arm_id(arm_id)

        def _work() -> None:
            client = self._compute_client(ref.subscription_id)
            client.virtual_machines.begin_restart(ref.resource_group, ref.name)

        await run_in_threadpool(_work)

    # ---------------------------------------------------------------------
    # v2.11.0 — Azure auto-shutdown schedule (Microsoft.DevTestLab/schedules).
    # Uses raw ARM REST + a DefaultAzureCredential-issued token so we don't
    # have to pull in azure-mgmt-devtestlabs just for one resource type.
    # ---------------------------------------------------------------------

    def _schedule_url(self, ref: VmRef) -> str:
        return (
            f"https://management.azure.com/subscriptions/{ref.subscription_id}"
            f"/resourceGroups/{ref.resource_group}"
            f"/providers/Microsoft.DevTestLab/schedules"
            f"/shutdown-computevm-{ref.name}"
            f"?api-version=2018-09-15"
        )

    def _arm_token(self) -> str:
        cred = self._cred()
        return cred.get_token("https://management.azure.com/.default").token  # type: ignore[attr-defined]

    async def get_auto_shutdown(self, arm_id: str) -> AutoShutdown | None:
        ref = parse_vm_arm_id(arm_id)
        import httpx

        url = self._schedule_url(ref)
        token = await run_in_threadpool(self._arm_token)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        body = resp.json()
        props = body.get("properties") or {}
        notif = props.get("notificationSettings") or {}
        return AutoShutdown(
            enabled=(str(props.get("status", "")).lower() == "enabled"),
            time_hhmm=str((props.get("dailyRecurrence") or {}).get("time", "")),
            timezone_id=str(props.get("timeZoneId", "")),
            notification_webhook_url=notif.get("webhookUrl") or None,
            notification_minutes=int(notif.get("timeInMinutes") or 30),
        )

    async def set_auto_shutdown(
        self, arm_id: str, schedule: AutoShutdown
    ) -> AutoShutdown:
        ref = parse_vm_arm_id(arm_id)
        import httpx

        # The schedule resource must be co-located with the VM. Read the
        # VM once to pin location — Azure rejects PUT with a mismatched
        # location if the schedule already exists elsewhere.
        vm = await self.get_vm(arm_id)
        payload: dict[str, Any] = {
            "location": vm.location,
            "properties": {
                "status": "Enabled" if schedule.enabled else "Disabled",
                "taskType": "ComputeVmShutdownTask",
                "dailyRecurrence": {"time": schedule.time_hhmm},
                "timeZoneId": schedule.timezone_id,
                "targetResourceId": arm_id,
            },
        }
        if schedule.notification_webhook_url:
            payload["properties"]["notificationSettings"] = {
                "status": "Enabled",
                "webhookUrl": schedule.notification_webhook_url,
                "timeInMinutes": schedule.notification_minutes,
            }
        url = self._schedule_url(ref)
        token = await run_in_threadpool(self._arm_token)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        resp.raise_for_status()
        # PUT returns the persisted resource — round-trip through our reader
        # so callers observe exactly what Azure stored, not our intent.
        fresh = await self.get_auto_shutdown(arm_id)
        if fresh is None:  # pragma: no cover — Azure lied
            raise RuntimeError("Azure accepted the schedule but the GET now 404s")
        return fresh

    async def delete_auto_shutdown(self, arm_id: str) -> None:
        ref = parse_vm_arm_id(arm_id)
        import httpx

        url = self._schedule_url(ref)
        token = await run_in_threadpool(self._arm_token)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 404:
            return
        resp.raise_for_status()

    # ---------------------------------------------------------------------
    # v2.12.0 — one-shot Run Command via ARM LRO.
    # POST accepts the script and returns 202 with an Azure-AsyncOperation
    # URL; we poll every 2s up to ~90s. On Succeeded we GET that same URL
    # for the ``value`` array, which is Azure's shape for the merged
    # per-status-code stdout/stderr buckets.
    # ---------------------------------------------------------------------

    async def run_command(
        self, arm_id: str, script: str, os_type: str
    ) -> RunCommandResult:
        ref = parse_vm_arm_id(arm_id)
        import time

        import httpx

        command_id = (
            "RunPowerShellScript"
            if os_type.lower().startswith("windows")
            else "RunShellScript"
        )
        run_url = (
            f"https://management.azure.com/subscriptions/{ref.subscription_id}"
            f"/resourceGroups/{ref.resource_group}"
            f"/providers/Microsoft.Compute/virtualMachines/{ref.name}"
            f"/runCommand?api-version=2024-11-01"
        )
        body = {"commandId": command_id, "script": [script]}
        token = await run_in_threadpool(self._arm_token)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(run_url, headers=headers, json=body)
            if resp.status_code not in (200, 201, 202):
                resp.raise_for_status()

            # If Azure returned the result inline (rare for runCommand but
            # possible on tiny fast VMs), skip polling.
            poll_url = resp.headers.get("Azure-AsyncOperation") or resp.headers.get(
                "Location"
            )
            body_json = _safe_json(resp)
            if body_json and "value" in body_json:
                stdout, stderr, code = _parse_run_command_value(body_json["value"])
                return RunCommandResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=code,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

            if not poll_url:
                raise RuntimeError(
                    "Azure runCommand returned no Azure-AsyncOperation/Location header",
                )

            # Poll — 2s cadence, ~90s cap. Each poll refreshes headers so
            # if Azure hands out a new location we follow it.
            deadline = started + 90.0
            while True:
                await asyncio.sleep(2.0)
                poll = await client.get(poll_url, headers=headers)
                poll.raise_for_status()
                poll_body = poll.json()
                status_val = str(poll_body.get("status", "")).lower()
                if status_val in {"succeeded", "failed", "canceled"}:
                    # Terminal state. Azure's runCommand emits the actual
                    # stdout/stderr inside the `properties.output.value`
                    # array once the async op finishes.
                    output = (poll_body.get("properties") or {}).get("output") or {}
                    value = output.get("value") or poll_body.get("value") or []
                    stdout, stderr, code = _parse_run_command_value(value)
                    err_ctx = poll_body.get("error")
                    if status_val == "failed" and err_ctx and not stderr:
                        stderr = str(err_ctx)
                    return RunCommandResult(
                        stdout=stdout,
                        stderr=stderr,
                        exit_code=code,
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                if time.monotonic() > deadline:
                    return RunCommandResult(
                        stdout="",
                        stderr="run-command exceeded 90s poll budget — the "
                        "command may still be running on the VM. Check the "
                        "Azure portal's Run Command tab for the final output.",
                        exit_code=None,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        timed_out=True,
                    )


def _safe_json(resp: object) -> dict[str, Any] | None:
    try:
        data = resp.json()  # type: ignore[attr-defined]
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _parse_run_command_value(
    value: list[dict[str, Any]] | Any,
) -> tuple[str, str, int | None]:
    """Split Azure's per-status ``value`` array into stdout, stderr, exit code.

    Each entry has a ``code`` like ``ComponentStatus/StdOut/succeeded`` and a
    ``message`` string. Linux + Windows share this shape. Exit code isn't
    surfaced directly by the LRO; we return None so the UI shows a neutral
    "completed" chip rather than fabricating a 0/1.
    """
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    if not isinstance(value, list):
        return "", "", None
    for entry in value:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code") or "")
        msg = str(entry.get("message") or "")
        if "StdOut" in code:
            stdout_parts.append(msg)
        elif "StdErr" in code:
            stderr_parts.append(msg)
    return "\n".join(stdout_parts).rstrip(), "\n".join(stderr_parts).rstrip(), None


def _collect_vms_for_sub(
    compute_client: object,
    network_client: object,
    subscription_id: str,
) -> list[VmSummary]:
    """Return one summary per VM in the sub. Runs sync inside a threadpool."""
    # NIC → VM public/private IP requires two lookups. We fetch all NICs +
    # PIPs in the sub once (cheap) and index them, so the per-VM cost is
    # constant. The Azure Compute list gives us OS + power state (with
    # instance view) in a single call when we ask.
    pip_by_id: dict[str, str] = {}
    for pip in network_client.public_ip_addresses.list_all():  # type: ignore[attr-defined]
        if pip.id and pip.ip_address:
            pip_by_id[pip.id.lower()] = pip.ip_address
    nic_by_id: dict[str, object] = {}
    for nic in network_client.network_interfaces.list_all():  # type: ignore[attr-defined]
        if nic.id:
            nic_by_id[nic.id.lower()] = nic

    out: list[VmSummary] = []
    for vm in compute_client.virtual_machines.list_all():  # type: ignore[attr-defined]
        # Instance view carries power state — one extra call per VM. Large
        # tenants might want a fan-out limit here later; today's shape is
        # dozens, not thousands.
        try:
            ref = parse_vm_arm_id(vm.id or "")
        except ValueError:
            continue
        summary = _summary_from_vm(
            vm=vm,
            subscription_id=subscription_id,
            resource_group=ref.resource_group,
            pip_by_id=pip_by_id,
            nic_by_id=nic_by_id,
            power_state="unknown",
        )
        try:
            iv = compute_client.virtual_machines.instance_view(  # type: ignore[attr-defined]
                ref.resource_group, ref.name
            )
            summary.power_state = _extract_power_state(iv)
        except Exception:
            pass
        out.append(summary)
    return out


def _hydrate_vm(
    compute_client: object,
    network_client: object,
    subscription_id: str,
    resource_group: str,
    name: str,
    *,
    expand_instance_view: bool,
) -> VmSummary:
    vm = compute_client.virtual_machines.get(  # type: ignore[attr-defined]
        resource_group, name, expand="instanceView" if expand_instance_view else None
    )
    pip_by_id: dict[str, str] = {}
    for pip in network_client.public_ip_addresses.list_all():  # type: ignore[attr-defined]
        if pip.id and pip.ip_address:
            pip_by_id[pip.id.lower()] = pip.ip_address
    nic_by_id: dict[str, object] = {}
    for nic in network_client.network_interfaces.list_all():  # type: ignore[attr-defined]
        if nic.id:
            nic_by_id[nic.id.lower()] = nic
    iv = getattr(vm, "instance_view", None)
    power = _extract_power_state(iv) if iv else "unknown"
    return _summary_from_vm(
        vm=vm,
        subscription_id=subscription_id,
        resource_group=resource_group,
        pip_by_id=pip_by_id,
        nic_by_id=nic_by_id,
        power_state=power,
    )


def _summary_from_vm(
    *,
    vm: object,
    subscription_id: str,
    resource_group: str,
    pip_by_id: dict[str, str],
    nic_by_id: dict[str, object],
    power_state: PowerState,
) -> VmSummary:
    # Public + private IP: pick the VM's primary NIC (or first if unmarked)
    # then read its ip_configurations. Public IPs are referenced by id, so
    # we look them up in the sub-wide index built above.
    public_ip: str | None = None
    private_ip: str | None = None
    nic_refs = getattr(getattr(vm, "network_profile", None), "network_interfaces", None) or []
    primary_nic = None
    for nic_ref in nic_refs:
        if getattr(nic_ref, "primary", False):
            primary_nic = nic_by_id.get((nic_ref.id or "").lower())
            break
    if primary_nic is None and nic_refs:
        primary_nic = nic_by_id.get((nic_refs[0].id or "").lower())
    if primary_nic is not None:
        for ip_cfg in getattr(primary_nic, "ip_configurations", None) or []:
            if not private_ip and getattr(ip_cfg, "private_ip_address", None):
                private_ip = ip_cfg.private_ip_address
            pip_ref = getattr(ip_cfg, "public_ip_address", None)
            if pip_ref and pip_ref.id and not public_ip:
                public_ip = pip_by_id.get(pip_ref.id.lower())
            if private_ip and public_ip:
                break

    storage = getattr(vm, "storage_profile", None)
    os_disk = getattr(storage, "os_disk", None) if storage else None
    image = getattr(storage, "image_reference", None) if storage else None
    os_type_raw = str(getattr(os_disk, "os_type", "") or "")
    os_offer = None
    if image is not None:
        offer = getattr(image, "offer", None)
        sku = getattr(image, "sku", None)
        if offer or sku:
            os_offer = " ".join(part for part in (offer, sku) if part)

    hardware = getattr(vm, "hardware_profile", None)
    size = str(getattr(hardware, "vm_size", "") or "")

    return VmSummary(
        arm_id=vm.id,
        name=vm.name,
        subscription_id=subscription_id,
        resource_group=resource_group,
        location=vm.location,
        size=size,
        os_type=os_type_raw.capitalize() if os_type_raw else "Unknown",
        os_offer=os_offer,
        power_state=power_state,
        public_ip=public_ip,
        private_ip=private_ip,
        tags=dict(vm.tags or {}),
    )


def _extract_power_state(instance_view: object) -> PowerState:
    """Instance view returns a list of statuses like ``PowerState/running``.

    Azure documents four VM states: running / stopped / deallocated /
    stopping — plus transient starting / deallocating that surface during
    LROs. Anything unrecognized falls back to unknown so the frontend
    doesn't crash on new SDK values.
    """
    for status in getattr(instance_view, "statuses", None) or []:
        code = str(getattr(status, "code", "") or "")
        if code.startswith("PowerState/"):
            key = code.split("/", 1)[1].lower()
            if key in {"running", "stopped", "deallocated", "starting", "stopping", "deallocating"}:
                return key  # type: ignore[return-value]
    return "unknown"


# ---------------------------------------------------------------------------
# Factory — mock in local-dev when no sub is configured, real otherwise.
# ---------------------------------------------------------------------------


_service: AzureArmService | None = None


def get_arm_service(settings: Settings) -> AzureArmService:
    """Cached singleton. Router calls this via a FastAPI dependency."""
    global _service
    if _service is None:
        if _should_use_mock(settings):
            from app.services.azure_arm_mock import MockAzureArmService

            _service = MockAzureArmService()
        else:
            _service = RealAzureArmService(settings)
    return _service


def _should_use_mock(settings: Settings) -> bool:
    # Local dev with no configured subs → mock. Anywhere else needs subs
    # to be configured; empty list just means the tab is inert but the
    # real client still gets constructed for potential future config.
    return settings.env == "local" and not settings.infra_subscriptions


def reset_arm_service_cache() -> None:
    """Test-only hook. Also used implicitly on process restart."""
    global _service
    _service = None
