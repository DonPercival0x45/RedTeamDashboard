"""Mock ARM service used by local dev — no Azure creds required.

Two fixture VMs (one running Linux, one deallocated Windows) so the
smoke checklist can exercise the whole start/stop/restart flow. Power
state is held in-process so the UI's optimistic transitions bear out.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from app.services.azure_arm import (
    AutoShutdown,
    PowerState,
    SubscriptionSummary,
    VmSummary,
    parse_vm_arm_id,
)

_MOCK_SUB = "00000000-0000-0000-0000-0000mock5qmock"
_MOCK_SUB_DISPLAY = "Local Mock Subscription"


def _fixtures() -> list[VmSummary]:
    return [
        VmSummary(
            arm_id=format_vm_arm_id_from_parts(_MOCK_SUB, "rtd-lab", "web-jumpbox"),
            name="web-jumpbox",
            subscription_id=_MOCK_SUB,
            resource_group="rtd-lab",
            location="centralus",
            size="Standard_B2s",
            os_type="Linux",
            os_offer="0001-com-ubuntu-server-jammy 22_04-lts-gen2",
            power_state="running",
            public_ip="203.0.113.10",
            private_ip="10.20.0.4",
            tags={"env": "lab", "owner": "recon"},
        ),
        VmSummary(
            arm_id=format_vm_arm_id_from_parts(_MOCK_SUB, "rtd-lab", "phish-host"),
            name="phish-host",
            subscription_id=_MOCK_SUB,
            resource_group="rtd-lab",
            location="eastus2",
            size="Standard_D2s_v5",
            os_type="Windows",
            os_offer="WindowsServer 2022-datacenter-azure-edition",
            power_state="deallocated",
            public_ip=None,
            private_ip="10.30.0.5",
            tags={"env": "lab", "owner": "phish"},
        ),
    ]


def format_vm_arm_id_from_parts(sub: str, rg: str, name: str) -> str:
    from app.services.azure_arm import VmRef, format_vm_arm_id

    return format_vm_arm_id(VmRef(subscription_id=sub, resource_group=rg, name=name))


class MockAzureArmService:
    def __init__(self) -> None:
        self._vms: dict[str, VmSummary] = {v.arm_id.lower(): v for v in _fixtures()}
        # v2.11.0: per-VM auto-shutdown state. Persists across get/set
        # cycles so the modal round-trip observably takes effect.
        self._schedules: dict[str, AutoShutdown] = {}
        self._lock = asyncio.Lock()

    async def list_subscriptions(self) -> list[SubscriptionSummary]:
        return [
            SubscriptionSummary(
                subscription_id=_MOCK_SUB,
                display_name=_MOCK_SUB_DISPLAY,
                state="Enabled",
            )
        ]

    async def list_all_vms(self) -> list[VmSummary]:
        async with self._lock:
            return list(self._vms.values())

    async def get_vm(self, arm_id: str) -> VmSummary:
        async with self._lock:
            vm = self._vms.get(arm_id.lower())
            if vm is None:
                # Case-insensitive fallback via ref
                ref = parse_vm_arm_id(arm_id)
                canonical = format_vm_arm_id_from_parts(
                    ref.subscription_id, ref.resource_group, ref.name
                ).lower()
                vm = self._vms.get(canonical)
            if vm is None:
                raise LookupError(f"unknown mock VM: {arm_id}")
            return vm

    async def start_vm(self, arm_id: str) -> None:
        await self._transition(arm_id, "starting", "running", delay_s=6.0)

    async def deallocate_vm(self, arm_id: str) -> None:
        await self._transition(arm_id, "deallocating", "deallocated", delay_s=6.0)

    async def restart_vm(self, arm_id: str) -> None:
        # Restart puts the row into ``stopping`` then back to running so
        # the UI's badge changes are visible during the smoke walk.
        await self._transition(arm_id, "stopping", "running", delay_s=6.0)

    async def _transition(
        self,
        arm_id: str,
        intermediate: PowerState,
        final: PowerState,
        *,
        delay_s: float,
    ) -> None:
        key = arm_id.lower()
        async with self._lock:
            vm = self._vms.get(key)
            if vm is None:
                raise LookupError(f"unknown mock VM: {arm_id}")
            self._vms[key] = replace(vm, power_state=intermediate)

        async def _settle() -> None:
            await asyncio.sleep(delay_s)
            async with self._lock:
                current = self._vms.get(key)
                if current is not None:
                    self._vms[key] = replace(current, power_state=final)

        # Fire-and-forget: mirrors the real Azure LRO semantics where the
        # POST returns 202 and the workload settles asynchronously.
        asyncio.create_task(_settle())

    # ---------------------------------------------------------------------
    # v2.11.0 — auto-shutdown mock. Real Azure round-trips the
    # Microsoft.DevTestLab/schedules resource; the mock just holds it
    # in-process so the modal's save/reload flow is exercisable offline.
    # ---------------------------------------------------------------------

    async def get_auto_shutdown(self, arm_id: str) -> AutoShutdown | None:
        # Confirm the VM exists (matches real service's 404 semantics for
        # unknown resources) and then look up the schedule.
        await self.get_vm(arm_id)
        async with self._lock:
            return self._schedules.get(arm_id.lower())

    async def set_auto_shutdown(
        self, arm_id: str, schedule: AutoShutdown
    ) -> AutoShutdown:
        await self.get_vm(arm_id)
        async with self._lock:
            self._schedules[arm_id.lower()] = schedule
            return schedule

    async def delete_auto_shutdown(self, arm_id: str) -> None:
        await self.get_vm(arm_id)
        async with self._lock:
            self._schedules.pop(arm_id.lower(), None)
