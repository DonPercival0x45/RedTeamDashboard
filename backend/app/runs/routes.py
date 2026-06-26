"""Run-enqueue endpoint for Project X-Ray.

Endpoints::

    POST   /projects/{slug}/runs                     -> enqueue run.start
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession, RedisClient
from app.core.config import settings
from app.models import ActorType, AuditLog, ProjectStatus
from app.orchestrator.llm import default_provider_model
from app.projects.routes import _get_project_or_404
from app.projects.schemas import RunModel, RunStart, RunStartResponse
from app.runs.events import encode_command
from app.runs.streams import inbound_stream, outbound_stream, store_run_model

router = APIRouter()


# ---------------------------------------------------------------------------
# Runs (enqueue run.start to the inbound stream)
# ---------------------------------------------------------------------------


def _check_provider_key_available(provider: str) -> None:
    """Raise 400 if the provider's credentials aren't set.

    Container Apps populates env vars from Key Vault refs; if the operator
    hasn't filled in the LLM key yet, the secret still reads as the
    ``PLACEHOLDER-set-after-deploy`` string. Treat that as missing too.
    """
    def _looks_placeholder(value: str) -> bool:
        return not value or value.startswith("PLACEHOLDER")

    if provider == "anthropic":
        if _looks_placeholder(settings.anthropic_api_key):
            raise HTTPException(
                status_code=400,
                detail="ANTHROPIC_API_KEY not configured for this deployment.",
            )
    elif provider == "openai":
        if _looks_placeholder(settings.openai_api_key):
            raise HTTPException(
                status_code=400,
                detail="OPENAI_API_KEY not configured for this deployment.",
            )
    elif provider == "azure" and (
        _looks_placeholder(settings.azure_openai_api_key)
        or _looks_placeholder(settings.azure_openai_endpoint)
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT not configured "
                "for this deployment."
            ),
        )
    # ollama is local — no key precheck.


@router.post(
    "/projects/{slug}/runs",
    response_model=RunStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_run(
    slug: str,
    body: RunStart,
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentUser,
) -> RunStartResponse:
    eng = _get_project_or_404(session, slug)
    if eng.status is not ProjectStatus.active:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project is {eng.status.value}; only active engagements "
                "accept new runs"
            ),
        )

    # Resolve effective model: body wins, else fall back to env defaults.
    if body.model is not None:
        provider, model_name = body.model.provider, body.model.name
    else:
        provider, model_name = default_provider_model()
    _check_provider_key_available(provider)
    effective_model = RunModel(provider=provider, name=model_name)

    thread_id = uuid.uuid4()
    # Stash the (provider, model) so the approval endpoint can echo it on
    # the resume envelope without redoing the resolution dance.
    store_run_model(
        redis_client,
        thread_id,
        provider=effective_model.provider,
        model_name=effective_model.name,
    )

    redis_client.xadd(
        inbound_stream(eng.id),
        encode_command(
            {
                "type": "run.start",
                "thread_id": str(thread_id),
                "prompt": body.prompt,
                "model": {
                    "provider": effective_model.provider,
                    "name": effective_model.name,
                },
            }
        ),
    )

    session.add(
        AuditLog(
            project_id=eng.id,
            actor_type=ActorType.user,
            actor_id=str(user.id),
            event_type="run.requested",
            payload={
                "thread_id": str(thread_id),
                "prompt_len": len(body.prompt),
                "model": {
                    "provider": effective_model.provider,
                    "name": effective_model.name,
                },
            },
        )
    )
    session.commit()

    return RunStartResponse(
        project_id=eng.id,
        thread_id=thread_id,
        events_stream=outbound_stream(eng.id),
        model=effective_model,
    )
