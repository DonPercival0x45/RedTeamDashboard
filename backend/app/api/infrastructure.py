"""v2.10.0 Infrastructure tab — admin-only VM inventory + power actions.

Every route is gated by :data:`CurrentAdminUser`. Every mutating action
records an ``audit_log`` row with ``event_type = infra.<verb>`` so the
existing settings/agent-runs page surfaces admin activity uniformly.

Power semantics:
- start → Azure ``begin_start`` (async LRO)
- stop  → ``begin_deallocate`` (frees compute cost — the user's request
  was Stop == deallocate, not power_off which keeps you paying)
- restart → ``begin_restart``

All three return 202 with an in-flight status; the frontend polls
:func:`get_vm` on a 15s cadence to observe the transition.

Auto-shutdown scheduling and the serial-console websocket are v2.11 +
v2.12 respectively — the button surfaces exist in the UI but are
disabled with a "coming soon" tooltip.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentAdminUser, DbSession
from app.core.config import Settings
from app.models import ActorType, AuditLog
from app.services.azure_arm import (
    AutoShutdown,
    AzureArmService,
    RunCommandResult,
    SubscriptionSummary,
    VmSummary,
    get_arm_service,
    parse_vm_arm_id,
)

router = APIRouter(prefix="/infrastructure", tags=["infrastructure"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Wire shapes (Pydantic) — kept as their own models so we can evolve the
# service dataclass without changing the API surface.
# ---------------------------------------------------------------------------


class SubscriptionRead(BaseModel):
    subscription_id: str
    display_name: str
    state: str


class VmRead(BaseModel):
    arm_id: str
    name: str
    subscription_id: str
    resource_group: str
    location: str
    size: str
    os_type: str
    os_offer: str | None
    power_state: str
    public_ip: str | None
    private_ip: str | None
    tags: dict[str, str]


class InfraStatusRead(BaseModel):
    configured: bool
    mock: bool
    subscription_count: int


class AutoShutdownRead(BaseModel):
    """Serialized ``Microsoft.DevTestLab/schedules`` view (v2.11.0).

    ``time_hhmm`` is a 4-digit local time ("1900"). ``timezone_id`` is a
    Windows time-zone id (Azure's native format — not IANA). No schedule
    on the VM → the API returns 404.
    """

    enabled: bool
    time_hhmm: str
    timezone_id: str
    notification_webhook_url: str | None = None
    notification_minutes: int = 30


class AutoShutdownWrite(BaseModel):
    enabled: bool = True
    time_hhmm: str
    timezone_id: str
    notification_webhook_url: str | None = None
    notification_minutes: int = 30


class RunCommandRequest(BaseModel):
    """POST body for the Connect drawer's one-shot run-command."""

    script: str


class RunCommandResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool


def _to_run_command_response(r: RunCommandResult) -> RunCommandResponse:
    return RunCommandResponse(
        stdout=r.stdout,
        stderr=r.stderr,
        exit_code=r.exit_code,
        duration_ms=r.duration_ms,
        timed_out=r.timed_out,
    )


def _to_schedule_read(s: AutoShutdown) -> AutoShutdownRead:
    return AutoShutdownRead(
        enabled=s.enabled,
        time_hhmm=s.time_hhmm,
        timezone_id=s.timezone_id,
        notification_webhook_url=s.notification_webhook_url,
        notification_minutes=s.notification_minutes,
    )


def _to_read(v: VmSummary) -> VmRead:
    return VmRead(
        arm_id=v.arm_id,
        name=v.name,
        subscription_id=v.subscription_id,
        resource_group=v.resource_group,
        location=v.location,
        size=v.size,
        os_type=v.os_type,
        os_offer=v.os_offer,
        power_state=v.power_state,
        public_ip=v.public_ip,
        private_ip=v.private_ip,
        tags=v.tags,
    )


def _to_sub_read(s: SubscriptionSummary) -> SubscriptionRead:
    return SubscriptionRead(
        subscription_id=s.subscription_id,
        display_name=s.display_name,
        state=s.state,
    )


def _service() -> AzureArmService:
    # Settings is a cheap dataclass to construct; we don't inject via
    # FastAPI to keep the route signatures short.
    from app.core.config import Settings as _S

    return get_arm_service(_S())


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("/status", response_model=InfraStatusRead)
async def get_status(_: CurrentAdminUser) -> InfraStatusRead:
    """Cheap health tile the frontend hits before painting the page."""
    settings = Settings()
    from app.services.azure_arm import _should_use_mock  # noqa: SLF001

    return InfraStatusRead(
        configured=bool(settings.infra_subscriptions) or _should_use_mock(settings),
        mock=_should_use_mock(settings),
        subscription_count=len(settings.infra_subscriptions),
    )


@router.get("/subscriptions", response_model=list[SubscriptionRead])
async def list_subscriptions(_: CurrentAdminUser) -> list[SubscriptionRead]:
    subs = await _service().list_subscriptions()
    return [_to_sub_read(s) for s in subs]


@router.get("/vms", response_model=list[VmRead])
async def list_vms(_: CurrentAdminUser) -> list[VmRead]:
    vms = await _service().list_all_vms()
    return [_to_read(v) for v in vms]


# ---------------------------------------------------------------------------
# v2.11.0 — auto-shutdown schedule (Microsoft.DevTestLab/schedules).
# Registered ABOVE the catch-all /vms/{arm_id:path} GET so FastAPI's
# ordered path-matching hits the specific suffix first. Move at your
# peril: a bare GET /vms/{arm}/auto-shutdown will otherwise land on
# get_vm and return the VM's own JSON.
# ---------------------------------------------------------------------------


@router.get("/vms/{arm_id:path}/auto-shutdown", response_model=AutoShutdownRead)
async def get_auto_shutdown(arm_id: str, _: CurrentAdminUser) -> AutoShutdownRead:
    normalized = _normalize_arm_id(arm_id)
    try:
        schedule = await _service().get_auto_shutdown(normalized)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        log.warning("infra_get_schedule_failed", arm_id=normalized, error=str(exc))
        raise HTTPException(status_code=502, detail=f"azure schedule read failed: {exc}") from exc
    if schedule is None:
        raise HTTPException(status_code=404, detail="no auto-shutdown schedule set")
    return _to_schedule_read(schedule)


@router.put("/vms/{arm_id:path}/auto-shutdown", response_model=AutoShutdownRead)
async def put_auto_shutdown(
    arm_id: str,
    body: AutoShutdownWrite,
    session: DbSession,
    user: CurrentAdminUser,
) -> AutoShutdownRead:
    normalized = _normalize_arm_id(arm_id)
    try:
        ref = parse_vm_arm_id(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    schedule = AutoShutdown(
        enabled=body.enabled,
        time_hhmm=body.time_hhmm,
        timezone_id=body.timezone_id,
        notification_webhook_url=body.notification_webhook_url,
        notification_minutes=body.notification_minutes,
    )
    try:
        saved = await _service().set_auto_shutdown(normalized, schedule)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        log.warning("infra_set_schedule_failed", arm_id=normalized, error=str(exc))
        raise HTTPException(status_code=502, detail=f"azure schedule write failed: {exc}") from exc
    session.add(
        AuditLog(
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="infra.vm.auto_shutdown_set",
            payload={
                "arm_id": normalized,
                "subscription_id": ref.subscription_id,
                "resource_group": ref.resource_group,
                "name": ref.name,
                "enabled": saved.enabled,
                "time_hhmm": saved.time_hhmm,
                "timezone_id": saved.timezone_id,
            },
        )
    )
    session.commit()
    return _to_schedule_read(saved)


@router.delete("/vms/{arm_id:path}/auto-shutdown", status_code=status.HTTP_204_NO_CONTENT)
async def delete_auto_shutdown(
    arm_id: str,
    session: DbSession,
    user: CurrentAdminUser,
) -> None:
    normalized = _normalize_arm_id(arm_id)
    try:
        ref = parse_vm_arm_id(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        await _service().delete_auto_shutdown(normalized)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        log.warning("infra_delete_schedule_failed", arm_id=normalized, error=str(exc))
        raise HTTPException(status_code=502, detail=f"azure schedule delete failed: {exc}") from exc
    session.add(
        AuditLog(
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="infra.vm.auto_shutdown_deleted",
            payload={
                "arm_id": normalized,
                "subscription_id": ref.subscription_id,
                "resource_group": ref.resource_group,
                "name": ref.name,
            },
        )
    )
    session.commit()


# v2.12.0 — one-shot Run Command. Registered here (above the catch-all
# GET /vms/{arm_id:path}) for the same reason auto-shutdown is: the
# greedy :path capture would otherwise swallow the /run-command suffix.
@router.post("/vms/{arm_id:path}/run-command", response_model=RunCommandResponse)
async def run_command(
    arm_id: str,
    body: RunCommandRequest,
    session: DbSession,
    user: CurrentAdminUser,
) -> RunCommandResponse:
    normalized = _normalize_arm_id(arm_id)
    try:
        ref = parse_vm_arm_id(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    script = (body.script or "").strip()
    if not script:
        raise HTTPException(status_code=400, detail="script must be non-empty")
    try:
        vm = await _service().get_vm(normalized)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        result = await _service().run_command(normalized, script, vm.os_type)
    except Exception as exc:
        log.warning(
            "infra_run_command_failed",
            arm_id=normalized,
            script_bytes=len(script),
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail=f"azure run-command failed: {exc}") from exc
    session.add(
        AuditLog(
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="infra.vm.run_command",
            payload={
                "arm_id": normalized,
                "subscription_id": ref.subscription_id,
                "resource_group": ref.resource_group,
                "name": ref.name,
                "os_type": vm.os_type,
                # Store the script and truncated stdout for audit — never
                # log secrets in scripts (admin's responsibility). stderr
                # captured too since failures are actionable.
                "script": script[:4000],
                "stdout": result.stdout[:4000],
                "stderr": result.stderr[:4000],
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "timed_out": result.timed_out,
            },
        )
    )
    session.commit()
    return _to_run_command_response(result)


@router.get("/vms/{arm_id:path}", response_model=VmRead)
async def get_vm(arm_id: str, _: CurrentAdminUser) -> VmRead:
    normalized = _normalize_arm_id(arm_id)
    try:
        vm = await _service().get_vm(normalized)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_read(vm)


# ---------------------------------------------------------------------------
# Actions — start / deallocate / restart. All log an audit row.
# ---------------------------------------------------------------------------


@router.post("/vms/{arm_id:path}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_vm(
    arm_id: str,
    session: DbSession,
    user: CurrentAdminUser,
) -> dict[str, Any]:
    return await _do_power_action(session, user, arm_id, action="start")


@router.post("/vms/{arm_id:path}/deallocate", status_code=status.HTTP_202_ACCEPTED)
async def deallocate_vm(
    arm_id: str,
    session: DbSession,
    user: CurrentAdminUser,
) -> dict[str, Any]:
    return await _do_power_action(session, user, arm_id, action="deallocate")


@router.post("/vms/{arm_id:path}/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart_vm(
    arm_id: str,
    session: DbSession,
    user: CurrentAdminUser,
) -> dict[str, Any]:
    return await _do_power_action(session, user, arm_id, action="restart")


async def _do_power_action(
    session: DbSession,
    user: Any,
    arm_id: str,
    *,
    action: str,
) -> dict[str, Any]:
    normalized = _normalize_arm_id(arm_id)
    try:
        ref = parse_vm_arm_id(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    svc = _service()
    try:
        if action == "start":
            await svc.start_vm(normalized)
        elif action == "deallocate":
            await svc.deallocate_vm(normalized)
        elif action == "restart":
            await svc.restart_vm(normalized)
        else:
            raise HTTPException(status_code=400, detail=f"unknown action: {action}")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        # Surface the Azure error verbatim to the admin — they need it to
        # debug (e.g. "OperationNotAllowed: A VM in state <x> can't be
        # started"). We still return 502 so retries make sense.
        log.warning(
            "infra_action_failed",
            action=action,
            arm_id=normalized,
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail=f"azure action failed: {exc}") from exc

    session.add(
        AuditLog(
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type=f"infra.vm.{action}",
            payload={
                "arm_id": normalized,
                "subscription_id": ref.subscription_id,
                "resource_group": ref.resource_group,
                "name": ref.name,
            },
        )
    )
    session.commit()
    return {"accepted": True, "action": action, "arm_id": normalized}


def _normalize_arm_id(arm_id: str) -> str:
    """FastAPI unescapes the ``:path`` capture, but callers occasionally
    hand us a leading slash and inconsistent casing. Emit the canonical
    shape so downstream index lookups stay stable."""
    from app.services.azure_arm import format_vm_arm_id

    try:
        ref = parse_vm_arm_id(arm_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return format_vm_arm_id(ref)
