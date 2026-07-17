"""Importing this package registers every model on ``Base.metadata``.

Alembic's env.py imports this module so autogenerate can see all tables.
"""
from app.models.agent_execution import (
    AgentExecution,
    AgentExecutionStatus,
    AgentTrigger,
)
from app.models.agent_model_preference import AgentModelPreference
from app.models.api_key import APIKey, APIKeyScope, scope_satisfies
from app.models.approval import Approval, ApprovalStatus, RiskLevel
from app.models.attachment import Attachment
from app.models.audit_log import ActorType, AuditLog
from app.models.authorization import Authorization
from app.models.conversation import (
    Conversation,
    ConversationContextType,
    ConversationMessage,
)
from app.models.engagement import (
    Engagement,
    EngagementStatus,
    EngagementTimeFrame,
    EngagementWorkState,
)
from app.models.engagement_strategy import (
    CoverageCategory,
    CoverageItem,
    CoverageStatus,
    EngagementCheckpoint,
    EngagementCompletionAction,
    EngagementCompletionDecision,
    EngagementObjective,
    EngagementStrategyRevision,
    ObjectivePriority,
    ObjectiveStatus,
    StrategyRevisionState,
    StrategySignal,
    StrategySignalStatus,
    WorkItem,
    WorkItemComment,
    WorkItemExecutor,
    WorkItemFinding,
    WorkItemFindingRelationship,
    WorkItemPriority,
    WorkItemResolution,
    WorkItemResult,
    WorkItemResultState,
    WorkItemStatus,
)
from app.models.entity import Entity
from app.models.entity_finding_link import EntityFindingLink
from app.models.entity_group import EntityGroup, EntityGroupMember
from app.models.finding import (
    Finding,
    FindingExclusion,
    FindingOrigin,
    FindingPhase,
    FindingStatus,
    Severity,
)
from app.models.finding_summary import FindingSummary
from app.models.integration import Integration, IntegrationPurpose, IntegrationType
from app.models.mcp_lease import MCPLease, MCPLeaseStatus
from app.models.observation import Observation
from app.models.observation_finding_link import ObservationFindingLink
from app.models.roadmap_suggestion import (
    RoadmapSuggestion,
    RoadmapSuggestionStatus,
)
from app.models.scope_item import ScopeItem, ScopeKind
from app.models.suggestion import (
    AgentName,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
)
from app.models.task import OwnerEligibility, Task, TaskKind, TaskStatus
from app.models.tool import (
    Tool,
    ToolInvocation,
    ToolInvocationStatus,
    ToolKind,
    ToolLane,
    ToolStatus,
    ToolTaskKind,
)
from app.models.user import User, UserRole

__all__ = [
    "APIKey",
    "APIKeyScope",
    "ActorType",
    "AgentExecution",
    "Attachment",
    "AgentExecutionStatus",
    "AgentModelPreference",
    "AgentName",
    "AgentTrigger",
    "Approval",
    "ApprovalStatus",
    "AuditLog",
    "Authorization",
    "EngagementCompletionAction",
    "Conversation",
    "ConversationContextType",
    "ConversationMessage",
    "CoverageCategory",
    "CoverageItem",
    "CoverageStatus",
    "Engagement",
    "EngagementCheckpoint",
    "EngagementCompletionDecision",
    "EngagementObjective",
    "EngagementStatus",
    "EngagementStrategyRevision",
    "EngagementTimeFrame",
    "EngagementWorkState",
    "Entity",
    "EntityFindingLink",
    "EntityGroup",
    "EntityGroupMember",
    "Finding",
    "FindingExclusion",
    "FindingOrigin",
    "FindingPhase",
    "FindingStatus",
    "FindingSummary",
    "Integration",
    "IntegrationPurpose",
    "IntegrationType",
    "MCPLease",
    "MCPLeaseStatus",
    "ObjectivePriority",
    "ObjectiveStatus",
    "Observation",
    "ObservationFindingLink",
    "OwnerEligibility",
    "RiskLevel",
    "RoadmapSuggestion",
    "RoadmapSuggestionStatus",
    "ScopeItem",
    "ScopeKind",
    "Severity",
    "StrategyRevisionState",
    "StrategySignal",
    "StrategySignalStatus",
    "Suggestion",
    "SuggestionKind",
    "SuggestionStatus",
    "Task",
    "TaskKind",
    "TaskStatus",
    "Tool",
    "ToolInvocation",
    "ToolInvocationStatus",
    "ToolKind",
    "ToolLane",
    "ToolStatus",
    "ToolTaskKind",
    "User",
    "UserRole",
    "WorkItem",
    "WorkItemComment",
    "WorkItemExecutor",
    "WorkItemFinding",
    "WorkItemFindingRelationship",
    "WorkItemPriority",
    "WorkItemResolution",
    "WorkItemResult",
    "WorkItemResultState",
    "WorkItemStatus",
    "scope_satisfies",
]
