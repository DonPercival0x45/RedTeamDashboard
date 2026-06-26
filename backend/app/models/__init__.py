"""Importing this package registers every model on ``Base.metadata``.

Alembic's env.py imports this module so autogenerate can see all tables.
"""
from app.auth.models import (
    APIKey,
    APIKeyScope,
    ActorType,
    AuditLog,
    Authorization,
    Approval,
    ApprovalStatus,
    ProviderKeyKind,
    RiskLevel,
    User,
    UserProviderKey,
    scope_satisfies,
)
from app.findings.models import Attachment, Finding, FindingPhase, FindingStatus, Severity
from app.observations.models import Observation
from app.projects.models import Project, ProjectStatus
from app.scope.models import ScopeItem, ScopeKind
from app.tasks.models import (
    AgentExecution,
    AgentExecutionStatus,
    AgentName,
    AgentTrigger,
    OwnerEligibility,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    Task,
    TaskKind,
    TaskStatus,
)

__all__ = [
    "APIKey",
    "APIKeyScope",
    "ActorType",
    "AgentExecution",
    "Attachment",
    "AgentExecutionStatus",
    "AgentName",
    "AgentTrigger",
    "Approval",
    "ApprovalStatus",
    "AuditLog",
    "Authorization",
    "Project",
    "ProjectStatus",
    "Finding",
    "FindingPhase",
    "FindingStatus",
    "Observation",
    "OwnerEligibility",
    "ProviderKeyKind",
    "RiskLevel",
    "ScopeItem",
    "ScopeKind",
    "Severity",
    "Suggestion",
    "SuggestionKind",
    "SuggestionStatus",
    "Task",
    "TaskKind",
    "TaskStatus",
    "User",
    "UserProviderKey",
    "scope_satisfies",
]
