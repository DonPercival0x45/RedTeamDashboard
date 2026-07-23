"""Wire-format models for engagements, scope items, and run kickoff."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import (
    EngagementArchitecture,
    EngagementPhase,
    EngagementStatus,
    EngagementTimeFrame,
    EngagementWorkState,
    ScopeKind,
)

LLMProvider = Literal[
    "anthropic",
    "openai",
    "azure",
    "ollama",
    "google",
    "xai",
    "mistral",
    "cohere",
    "together",
    "groq",
    "deepseek",
    "custom",
]


class InitialScopeItemCreate(BaseModel):
    """Client-defined scope staged with a new engagement."""

    kind: ScopeKind
    value: str = Field(min_length=1, max_length=500)
    is_exclusion: bool = False
    note: str | None = Field(default=None, max_length=500)

    @field_validator("value")
    @classmethod
    def _strip_nonblank_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("initial scope value cannot be blank")
        return normalized


class EngagementCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(
        default=None,
        max_length=200,
        description="Optional. Auto-generated from `name` if omitted.",
    )
    description: str | None = Field(
        default=None, description="Optional free-text engagement details."
    )
    time_frame: EngagementTimeFrame = Field(
        default=EngagementTimeFrame.point_in_time,
        description=(
            "Scheduling label. `custom` requires both `start_date` and "
            "`end_date`. Metadata only — does not drive the orchestrator yet."
        ),
    )
    start_date: date | None = None
    end_date: date | None = None
    initial_scope: list[InitialScopeItemCreate] = Field(
        default_factory=list,
        max_length=1000,
        description=(
            "Optional client-defined scope persisted atomically with the engagement."
        ),
    )
    intelligence_architecture: EngagementArchitecture | None = Field(
        default=None,
        description=(
            "Legacy orchestration or the v3 intelligence plane. Omit to let "
            "the backend pick from ``default_intelligence_architecture`` "
            "(v3 by default post-C6c). Explicit values still win."
        ),
    )
    methodology_slug: str | None = Field(default=None, max_length=120)
    methodology_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_custom_dates(self) -> EngagementCreate:
        if self.time_frame is EngagementTimeFrame.custom:
            if self.start_date is None or self.end_date is None:
                raise ValueError("time_frame='custom' requires both start_date and end_date")
            if self.end_date < self.start_date:
                raise ValueError("end_date cannot be before start_date")

        if self.methodology_version is not None and not self.methodology_slug:
            raise ValueError("methodology_version requires methodology_slug")
        # The v3-needs-methodology check runs only when the caller EXPLICITLY
        # asked for v3. When ``intelligence_architecture`` is None (falls
        # through to the settings default), the endpoint resolves it and
        # silently downshifts to legacy if the methodology isn't there —
        # C6c's compat-preserving default. That resolution can't happen
        # here because it needs ``settings``.
        if (
            self.intelligence_architecture is EngagementArchitecture.v3
            and not self.methodology_slug
        ):
            raise ValueError(
                "v3 engagement creation requires methodology_slug"
            )

        seen: set[tuple[ScopeKind, str, bool]] = set()
        for item in self.initial_scope:
            key = (item.kind, item.value, item.is_exclusion)
            if key in seen:
                disposition = "exclusion" if item.is_exclusion else "included target"
                raise ValueError(
                    f"duplicate initial_scope item: {item.kind.value}:{item.value} "
                    f"already exists as an {disposition}"
                )
            seen.add(key)
        return self


class EngagementUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: EngagementStatus | None = Field(
        default=None,
        description=(
            "Only `active` or `archived` are accepted via PATCH. Use "
            "POST /engagements/{slug}/flush for irreversible deletion."
        ),
    )
    auto_assess_enabled: bool | None = Field(
        default=None,
        description=(
            "Kill-switch for automatic background strategic generation "
            "(finding watcher + auto-reassess). False = no LLM tokens spent "
            "on auto-generated suggestions; the manual Analyze button is "
            "unaffected."
        ),
    )


class EngagementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    description: str | None = None
    status: EngagementStatus
    work_state: EngagementWorkState = EngagementWorkState.active
    work_state_version: int = 1
    auto_assess_enabled: bool = True
    intelligence_architecture: EngagementArchitecture = EngagementArchitecture.legacy
    converted_to_v3_at: datetime | None = None
    phase: EngagementPhase = EngagementPhase.baseline
    baseline_completed_at: datetime | None = None
    methodology_id: UUID | None = None
    methodology_selected_at: datetime | None = None
    time_frame: EngagementTimeFrame
    start_date: date | None
    end_date: date | None
    created_by: UUID | None
    archived_at: datetime | None
    flushed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # v1.4.5: scope quick-actions. Surfaces scope size on the engagement
    # list cards so analysts can spot empty / outlier engagements at a
    # glance. ``scope_count`` counts the actionable (non-exclusion) items;
    # ``exclusion_count`` counts !-marked items. Populated by
    # ``list_engagements``, ``get_engagement``, and ``update_engagement``
    # (cheap aggregate, default 0 so existing test fixtures still construct
    # cleanly).
    scope_count: int = 0
    exclusion_count: int = 0
    # v2.4.0: whether the engagement has a `state = current` strategy
    # revision. Frontend combines this with scope_count + start_date to
    # decide whether the engagement should render as "pending" (setup
    # not complete) rather than "active" on the list page.
    has_strategy: bool = False


class ScopeItemCreate(BaseModel):
    kind: ScopeKind
    value: str = Field(min_length=1, max_length=500)
    is_exclusion: bool = False
    note: str | None = Field(default=None, max_length=500)
    # v1.4.13: provenance (roadmap #5). "defined" = client-provided,
    # "found" = added from findings. Defaults to "defined".
    source: Literal["defined", "found"] = "defined"


class ScopeItemUpdate(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=500)
    is_exclusion: bool | None = None
    note: str | None = Field(default=None, max_length=500)


class ScopeItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    kind: ScopeKind
    value: str
    is_exclusion: bool
    note: str | None
    source: str = "defined"
    created_at: datetime
    updated_at: datetime


class ScopeImportRequest(BaseModel):
    """Bulk-import body: a free-form blob the parser turns into ScopeItems.

    Whatever the analyst pasted/uploaded — .txt or .csv content, mixed kinds,
    optional ``!`` exclusions, ``#`` comments — goes straight in here.
    """

    text: str = Field(min_length=1, max_length=200_000)


class ScopeImportPreviewRow(BaseModel):
    line: int
    value: str
    kind: ScopeKind
    is_exclusion: bool


class ScopeImportErrorRow(BaseModel):
    line: int
    raw: str
    reason: str


class ScopeImportDuplicateRow(BaseModel):
    line: int
    value: str
    kind: ScopeKind
    is_exclusion: bool


class ScopeImportPreview(BaseModel):
    """Shape returned by ``?dry_run=true`` — nothing persisted."""

    preview: list[ScopeImportPreviewRow]
    errors: list[ScopeImportErrorRow]
    would_create: int


class ScopeImportResult(BaseModel):
    """Shape returned by the real commit."""

    created: list[ScopeItemRead]
    errors: list[ScopeImportErrorRow]
    duplicates: list[ScopeImportDuplicateRow]


class RunModel(BaseModel):
    """Per-run LLM choice — overrides the worker's env defaults."""

    provider: LLMProvider
    name: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Model id passed to the provider's SDK (e.g. 'claude-opus-4-7', "
            "'gpt-4o-mini'). Not whitelisted server-side — model names churn "
            "faster than this repo."
        ),
    )
    # v1.4.12: select a specific cached provider key by id (roadmap #3 —
    # "keys for specific tasks"). Omit / null to keep the MRU behavior.
    key_id: UUID | None = None


class RunStart(BaseModel):
    prompt: str = Field(min_length=1)
    model: RunModel | None = Field(
        default=None,
        description=(
            "Optional per-run LLM. If omitted, the worker uses its env "
            "defaults (LLM_PROVIDER + provider-specific model env)."
        ),
    )


class RunStartResponse(BaseModel):
    engagement_id: UUID
    thread_id: UUID
    events_stream: str
    model: RunModel
    "The effective model used for this run (echoes the request, or the env default)."
