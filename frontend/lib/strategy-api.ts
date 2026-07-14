import { API_BASE_URL } from "@/lib/config";
import { ApiError, authHeaders } from "@/lib/api";
import type {
  StrategistActionResult,
  StrategistChatResponse,
  StrategistChatState,
  StrategistRunResponse,
  StrategistSummary,
} from "@/lib/strategist-types";
import type {
  Checkpoint,
  CompletionDecision,
  CompletionMutationResponse,
  CompletionReadiness,
  CoverageItem,
  CoverageStatus,
  Objective,
  ObjectiveCreate,
  ObjectiveUpdate,
  ResumeBriefing,
  StrategyRevision,
  StrategyRevisionCreate,
  StrategySignal,
  WorkItem,
  WorkItemCreate,
  WorkItemFilters,
  WorkItemResolution,
  WorkItemResult,
  WorkItemRollup,
  WorkItemUpdate,
} from "@/lib/strategy-types";

async function strategyRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(await authHeaders()),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    let detail: unknown = text;
    try {
      detail = text ? JSON.parse(text) : text;
    } catch {
      // Keep the raw server response.
    }
    throw new ApiError(response.status, response.statusText, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});

export function getCurrentStrategy(slug: string): Promise<StrategyRevision | null> {
  return strategyRequest<StrategyRevision | null>(
    `/engagements/${encodeURIComponent(slug)}/strategy`,
  );
}

export function listStrategyRevisions(slug: string): Promise<StrategyRevision[]> {
  return strategyRequest<StrategyRevision[]>(
    `/engagements/${encodeURIComponent(slug)}/strategy/revisions`,
  );
}

export function createStrategyRevision(
  slug: string,
  body: StrategyRevisionCreate,
): Promise<StrategyRevision> {
  return strategyRequest<StrategyRevision>(
    `/engagements/${encodeURIComponent(slug)}/strategy/revisions`,
    json(body),
  );
}

export function decideStrategyRevision(
  slug: string,
  revisionId: string,
  action: "accept" | "reject" | "restore",
  body: { based_on_revision_id?: string | null; reason?: string | null } = {},
): Promise<StrategyRevision> {
  return strategyRequest<StrategyRevision>(
    `/engagements/${encodeURIComponent(slug)}/strategy/revisions/${revisionId}/${action}`,
    json(body),
  );
}

export function listObjectives(slug: string): Promise<Objective[]> {
  return strategyRequest<Objective[]>(
    `/engagements/${encodeURIComponent(slug)}/objectives`,
  );
}

export function createObjective(slug: string, body: ObjectiveCreate): Promise<Objective> {
  return strategyRequest<Objective>(
    `/engagements/${encodeURIComponent(slug)}/objectives`,
    json(body),
  );
}

export function updateObjective(
  slug: string,
  objectiveId: string,
  body: ObjectiveUpdate,
): Promise<Objective> {
  return strategyRequest<Objective>(
    `/engagements/${encodeURIComponent(slug)}/objectives/${objectiveId}`,
    { method: "PATCH", body: JSON.stringify(body) },
  );
}

export function transitionObjective(
  slug: string,
  objectiveId: string,
  action: "complete" | "reopen",
  expectedRowVersion: number,
  reason?: string,
): Promise<Objective> {
  return strategyRequest<Objective>(
    `/engagements/${encodeURIComponent(slug)}/objectives/${objectiveId}/${action}`,
    json({ expected_row_version: expectedRowVersion, reason: reason || null }),
  );
}

export function cancelObjective(
  slug: string,
  objectiveId: string,
  expectedRowVersion: number,
  reason?: string,
): Promise<Objective> {
  return strategyRequest<Objective>(
    `/engagements/${encodeURIComponent(slug)}/objectives/${objectiveId}`,
    {
      method: "DELETE",
      body: JSON.stringify({
        expected_row_version: expectedRowVersion,
        reason: reason || null,
      }),
    },
  );
}

export function listWorkItems(
  slug: string,
  filters: WorkItemFilters = {},
): Promise<WorkItem[]> {
  const query = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const suffix = query.size ? `?${query.toString()}` : "";
  return strategyRequest<WorkItem[]>(
    `/engagements/${encodeURIComponent(slug)}/work-items${suffix}`,
  );
}

export function getWorkItem(workItemId: string): Promise<WorkItem> {
  return strategyRequest<WorkItem>(`/work-items/${workItemId}`);
}

export function createWorkItem(slug: string, body: WorkItemCreate): Promise<WorkItem> {
  return strategyRequest<WorkItem>(
    `/engagements/${encodeURIComponent(slug)}/work-items`,
    json(body),
  );
}

export function updateWorkItem(
  workItemId: string,
  body: WorkItemUpdate,
): Promise<WorkItem> {
  return strategyRequest<WorkItem>(`/work-items/${workItemId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function transitionWorkItem(
  workItemId: string,
  action: "start" | "defer" | "reopen" | "cancel",
  expectedRowVersion: number,
  reason?: string,
): Promise<WorkItem> {
  return strategyRequest<WorkItem>(
    `/work-items/${workItemId}/${action}`,
    json({ expected_row_version: expectedRowVersion, reason: reason || null }),
  );
}

export function blockWorkItem(
  workItemId: string,
  expectedRowVersion: number,
  reason: string,
): Promise<WorkItem> {
  return strategyRequest<WorkItem>(
    `/work-items/${workItemId}/block`,
    json({ expected_row_version: expectedRowVersion, reason }),
  );
}

export function resolveWorkItem(
  workItemId: string,
  expectedRowVersion: number,
  outcome: WorkItemResolution,
  note?: string,
): Promise<WorkItem> {
  return strategyRequest<WorkItem>(
    `/work-items/${workItemId}/resolve`,
    json({
      expected_row_version: expectedRowVersion,
      outcome,
      note: note || null,
      evidence_refs: [],
    }),
  );
}

export function listWorkItemResults(workItemId: string): Promise<WorkItemResult[]> {
  return strategyRequest<WorkItemResult[]>(`/work-items/${workItemId}/results`);
}

export function createWorkItemResult(
  workItemId: string,
  body: { summary: string; structured?: Record<string, unknown> },
): Promise<WorkItemResult> {
  return strategyRequest<WorkItemResult>(
    `/work-items/${workItemId}/results`,
    json({ ...body, evidence_refs: [] }),
  );
}

export function decideWorkItemResult(
  resultId: string,
  action: "accept" | "reject",
  body: Record<string, unknown>,
): Promise<unknown> {
  return strategyRequest(`/work-item-results/${resultId}/${action}`, json(body));
}

export function getWorkItemRollup(slug: string): Promise<WorkItemRollup> {
  return strategyRequest<WorkItemRollup>(
    `/engagements/${encodeURIComponent(slug)}/work-item-rollup`,
  );
}

export function listStrategySignals(slug: string): Promise<StrategySignal[]> {
  return strategyRequest<StrategySignal[]>(
    `/engagements/${encodeURIComponent(slug)}/strategy/signals`,
  );
}

export function decideStrategySignal(
  signalId: string,
  action: "incorporate" | "dismiss",
  reason?: string,
): Promise<StrategySignal> {
  return strategyRequest<StrategySignal>(
    `/strategy-signals/${signalId}/${action}`,
    json({ reason: reason || null }),
  );
}

export function getResumeBriefing(slug: string): Promise<ResumeBriefing> {
  return strategyRequest<ResumeBriefing>(
    `/engagements/${encodeURIComponent(slug)}/resume`,
  );
}

export function listCheckpoints(slug: string): Promise<Checkpoint[]> {
  return strategyRequest<Checkpoint[]>(
    `/engagements/${encodeURIComponent(slug)}/checkpoints`,
  );
}

export function createCheckpoint(slug: string, narrative?: string): Promise<Checkpoint> {
  return strategyRequest<Checkpoint>(
    `/engagements/${encodeURIComponent(slug)}/checkpoints`,
    json({ narrative: narrative || null }),
  );
}

export function listCoverage(
  slug: string,
  filters: { status?: CoverageStatus; category?: string } = {},
): Promise<CoverageItem[]> {
  const query = new URLSearchParams();
  if (filters.status) query.set("status", filters.status);
  if (filters.category) query.set("category", filters.category);
  const suffix = query.size ? `?${query.toString()}` : "";
  return strategyRequest<CoverageItem[]>(
    `/engagements/${encodeURIComponent(slug)}/coverage${suffix}`,
  );
}

export function createCoverageItem(
  slug: string,
  body: {
    objective_id?: string | null;
    scope_item_id?: string | null;
    target_kind: string;
    target_key: string;
    activity_category: string;
    status?: CoverageStatus;
    reason?: string | null;
  },
): Promise<CoverageItem> {
  return strategyRequest<CoverageItem>(
    `/engagements/${encodeURIComponent(slug)}/coverage`,
    json({ ...body, supporting_refs: [] }),
  );
}

export function updateCoverageItem(
  slug: string,
  item: CoverageItem,
  status: CoverageStatus,
  reason?: string,
): Promise<CoverageItem> {
  return strategyRequest<CoverageItem>(
    `/engagements/${encodeURIComponent(slug)}/coverage/${item.id}`,
    {
      method: "PATCH",
      body: JSON.stringify({
        expected_row_version: item.row_version,
        status,
        reason: reason || null,
      }),
    },
  );
}

export function getCompletionReadiness(slug: string): Promise<CompletionReadiness> {
  return strategyRequest<CompletionReadiness>(
    `/engagements/${encodeURIComponent(slug)}/completion/readiness`,
  );
}

export function listCompletionDecisions(slug: string): Promise<CompletionDecision[]> {
  return strategyRequest<CompletionDecision[]>(
    `/engagements/${encodeURIComponent(slug)}/completion/decisions`,
  );
}

export function startCompletionReview(
  slug: string,
  readiness: CompletionReadiness,
): Promise<CompletionMutationResponse> {
  return strategyRequest<CompletionMutationResponse>(
    `/engagements/${encodeURIComponent(slug)}/completion/review`,
    json({
      expected_work_state_version: readiness.work_state_version,
      readiness_hash: readiness.readiness_hash,
      idempotency_key: crypto.randomUUID(),
    }),
  );
}

export function approveCompletion(
  slug: string,
  readiness: CompletionReadiness,
  acceptedExceptions: Array<{
    ref: { type: string; id: string };
    rationale: string;
  }> = [],
): Promise<CompletionMutationResponse> {
  return strategyRequest<CompletionMutationResponse>(
    `/engagements/${encodeURIComponent(slug)}/completion/approve`,
    json({
      expected_work_state_version: readiness.work_state_version,
      readiness_hash: readiness.readiness_hash,
      idempotency_key: crypto.randomUUID(),
      accepted_exceptions: acceptedExceptions,
    }),
  );
}

export function runEngagementStrategist(
  slug: string,
  mode: "generate-initial" | "recommend" | "reassess" | "review-completion",
): Promise<StrategistRunResponse> {
  return strategyRequest<StrategistRunResponse>(
    `/engagements/${encodeURIComponent(slug)}/strategy/${mode}`,
    { method: "POST" },
  );
}

export function getStrategistChat(slug: string): Promise<StrategistChatState> {
  return strategyRequest<StrategistChatState>(
    `/engagements/${encodeURIComponent(slug)}/strategy/chat`,
  );
}

export function postStrategistChat(
  slug: string,
  message: string,
  conversationId?: string | null,
): Promise<StrategistChatResponse> {
  return strategyRequest<StrategistChatResponse>(
    `/engagements/${encodeURIComponent(slug)}/strategy/chat`,
    json({ message, conversation_id: conversationId || null }),
  );
}

export function decideStrategistChatAction(
  slug: string,
  messageId: string,
  actionIndex: number,
  action: "accept" | "deny",
): Promise<StrategistActionResult> {
  return strategyRequest<StrategistActionResult>(
    `/engagements/${encodeURIComponent(slug)}/strategy/chat/messages/${messageId}/actions/${action}`,
    json({ action_index: actionIndex }),
  );
}

export function summarizeStrategistChat(slug: string): Promise<StrategistSummary> {
  return strategyRequest<StrategistSummary>(
    `/engagements/${encodeURIComponent(slug)}/strategy/chat/summarize`,
    { method: "POST" },
  );
}

export function clearStrategistChat(slug: string): Promise<void> {
  return strategyRequest<void>(
    `/engagements/${encodeURIComponent(slug)}/strategy/chat`,
    { method: "DELETE" },
  );
}

export function reopenCompletion(
  slug: string,
  readiness: CompletionReadiness,
  priorCompletionDecisionId: string,
  reason: string,
): Promise<CompletionMutationResponse> {
  return strategyRequest<CompletionMutationResponse>(
    `/engagements/${encodeURIComponent(slug)}/completion/reopen`,
    json({
      prior_completion_decision_id: priorCompletionDecisionId,
      expected_work_state_version: readiness.work_state_version,
      reason,
      idempotency_key: crypto.randomUUID(),
    }),
  );
}
