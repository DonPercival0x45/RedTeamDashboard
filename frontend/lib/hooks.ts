"use client";

// v1.0.0: TanStack Query hooks over the api.ts fetch wrappers.
//
// Design:
//   - Query keys are flat tuples; invalidation is straightforward.
//   - Polling intervals live on the hook, not the caller. Hot entities poll
//     at a fast tick; cold ones don't poll at all. Function-form intervals
//     stop polling when there's nothing running to watch (cheap).
//   - Mutations invalidate the affected list keys; optimistic updates go
//     inline at the caller where the previous/next state is obvious.

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  approveTool,
  archiveEngagement,
  acceptFindingChatAction,
  denyFindingChatAction,
  askFindingChat,
  cancelAgentExecution,
  cancelTask,
  clearAgentConfiguration,
  clearFindingChat,
  summarizeFindingChat,
  createIntegration,
  downloadAgentConfigurations,
  importAgentConfigurations,
  listAgentConfigurations,
  putAgentConfiguration,
  createObservation,
  deallocateVm,
  deleteAutoShutdown,
  deleteIntegration,
  createScopeItem,
  decideApproval,
  deleteObservation,
  deleteProviderKey,
  linkObservationFinding,
  unlinkObservationFinding,
  deleteScopeItem,
  deleteTool,
  flushEngagement,
  getContributionsEntries,
  getContributionsHeatmap,
  getEngagement,
  getEngagementCosts,
  getEngagementDiagnostics,
  getAutoShutdown,
  getEngagementStatus,
  getGlobalAgentRunSteps,
  getInfraStatus,
  getStatusSteps,
  getMe,
  getReportReadiness,
  listGlobalAgentRuns,
  listAdminUsers,
  listAuthorizations,
  listEngagements,
  listEntities,
  listEntityDuplicateCandidates,
  listFindings,
  getFinding,
  getFindingActivity,
  getFindingChat,
  listFindingContextCandidates,
  listInfraSubscriptions,
  listIntegrations,
  listObservations,
  listProviderKeys,
  listVms,
  listOrchestratorTools,
  listPendingApprovals,
  fetchEngagementLog,
  fetchFindingsOverTime,
  fetchScanCoverage,
  fetchSeverityBreakdown,
  fetchTopFindings,
  listEngagementAttribution,
  listRoadmapSuggestions,
  listRunningTasks,
  listScope,
  listStoredEntities,
  listTasks,
  listToolInvocations,
  listTools,
  promoteFindingContext,
  putAutoShutdown,
  restartVm,
  runCommand,
  retryAgentExecution,
  retryTask,
  revokeAuthorization,
  revokeTool,
  startVm,
  updateEngagement,
  updateIntegration,
  updateMyPreferences,
  updateUserRole,
  updateUserActive,
} from "@/lib/api";
import { loadReleases } from "@/lib/release-notes";
import type {
  AgentConfigExport,
  AgentConfigImportResult,
  AgentConfigPut,
  AgentConfigRead,
  ContributionSource,
  Finding,
  FindingChatActionResponse,
  FindingChatResponse,
  FindingChatState,
  Integration,
  Observation,
  RoadmapListFilters,
  ScopeItem,
  StatusKind,
  Task,
  TaskStatus,
  ToolInvocationRead,
  ToolStatus,
  UserRole,
} from "@/lib/types";

export const qk = {
  me: () => ["me"] as const,
  releases: () => ["releases"] as const,
  providerKeys: () => ["provider-keys"] as const,
  agentConfigurations: () => ["agent-configurations"] as const,
  adminUsers: () => ["admin-users"] as const,
  integrations: () => ["integrations"] as const,
  roadmapSuggestions: (filters: RoadmapListFilters | undefined) =>
    ["roadmap-suggestions", filters] as const,
  authorizations: (engagementId: string, active?: boolean) =>
    ["authorizations", engagementId, { active }] as const,
  pendingApprovals: () => ["approvals", "pending"] as const,
  engagements: () => ["engagements"] as const,
  engagement: (slug: string) => ["engagement", slug] as const,
  reportReadiness: (slug: string) => ["report-readiness", slug] as const,
  findings: (slug: string) => ["findings", slug] as const,
  diagnostics: (slug: string) => ["diagnostics", slug] as const,
  finding: (id: string) => ["finding", id] as const,
  findingActivity: (id: string) => ["finding-activity", id] as const,
  findingChat: (id: string) => ["finding-chat", id] as const,
  findingContext: (id: string) => ["finding-context", id] as const,
  tasks: (slug: string, status?: TaskStatus) =>
    ["tasks", slug, { status: status ?? null }] as const,
  observations: (slug: string) => ["observations", slug] as const,
  scope: (slug: string) => ["scope", slug] as const,
  entities: (slug: string) => ["entities", slug] as const,
  storedEntities: (slug: string, includeSuppressed = false) =>
    ["stored-entities", slug, { includeSuppressed }] as const,
  entityDuplicateCandidates: (slug: string) =>
    ["entity-duplicate-candidates", slug] as const,
  contributionsHeatmap: (
    slug: string,
    filters: { actorId: string | null; source: string | null },
  ) => ["contributions-heatmap", slug, filters] as const,
  contributionsEntries: (
    slug: string,
    filters: {
      date: string;
      actorId: string | null;
      source: string | null;
    },
  ) => ["contributions-entries", slug, filters] as const,
  engagementStatus: (slug: string) =>
    ["engagement-status", slug] as const,
  statusSteps: (slug: string, kind: string, entityId: string) =>
    ["status-steps", slug, kind, entityId] as const,
  globalAgentRuns: () => ["global-agent-runs"] as const,
  globalAgentRunSteps: (executionId: string) =>
    ["global-agent-run-steps", executionId] as const,
  engagementCosts: (slug: string) =>
    ["engagement-costs", slug] as const,
  tools: (opts: { status?: ToolStatus; first_party?: boolean }) =>
    ["tools", opts] as const,
  orchestratorTools: () => ["orchestrator-tools"] as const,
  toolInvocations: (slug: string) =>
    ["tool-invocations", slug] as const,
  runningTasks: () => ["running-tasks"] as const,
  engagementAttribution: (slug: string) =>
    ["engagement-attribution", slug] as const,
  analyticsFindingsOverTime: (
    engagement: string | null,
    opts: import("@/lib/api").FindingsOverTimeOpts,
  ) =>
    [
      "analytics",
      "findings-over-time",
      engagement ?? "all",
      opts.period ?? "week",
      opts.points ?? 12,
      opts.start ?? null,
      opts.end ?? null,
    ] as const,
  analyticsSeverityBreakdown: (engagement: string | null) =>
    ["analytics", "severity-breakdown", engagement ?? "all"] as const,
  analyticsScanCoverage: (engagement: string | null) =>
    ["analytics", "scan-coverage", engagement ?? "all"] as const,
  analyticsTopFindings: (engagement: string | null, limit: number) =>
    ["analytics", "top-findings", engagement ?? "all", limit] as const,
  analyticsEngagementLog: (engagement: string | null, limit: number) =>
    ["analytics", "engagement-log", engagement ?? "all", limit] as const,
  infraStatus: () => ["infra", "status"] as const,
  infraSubscriptions: () => ["infra", "subscriptions"] as const,
  vms: () => ["infra", "vms"] as const,
  vm: (armId: string) => ["infra", "vm", armId] as const,
  autoShutdown: (armId: string) => ["infra", "auto-shutdown", armId] as const,
};

export function useEngagements() {
  return useQuery({
    queryKey: qk.engagements(),
    queryFn: () => listEngagements(),
  });
}

export function useEngagement(slug: string) {
  return useQuery({
    queryKey: qk.engagement(slug),
    queryFn: () => getEngagement(slug),
  });
}

export function useDiagnostics(slug: string) {
  return useQuery({
    queryKey: qk.diagnostics(slug),
    queryFn: () => getEngagementDiagnostics(slug),
  });
}

export function useFindings(slug: string) {
  // Findings are cached across route changes so navigating away and back
  // is instant. The SSE stream in app/e/page.tsx merges new findings
  // into this cache via qc.setQueryData(qk.findings(slug), ...).
  return useQuery({
    queryKey: qk.findings(slug),
    queryFn: () => listFindings(slug),
  });
}

export function useTasks(slug: string, status?: TaskStatus) {
  return useQuery({
    queryKey: qk.tasks(slug, status),
    queryFn: () => listTasks(slug, status),
    enabled: Boolean(slug),
    refetchInterval: (query) => {
      const tasks = query.state.data as Task[] | undefined;
      return tasks?.some((task) =>
        ["pending", "dispatched", "running"].includes(task.status),
      )
        ? 4000
        : false;
    },
  });
}

// v2.4.0 — cross-engagement running tasks for the Automation running-jobs
// banner. Polls at 4s while any in-flight tasks exist; drops to focus-only
// refresh once the queue is empty.
export function useRunningTasks() {
  return useQuery({
    queryKey: qk.runningTasks(),
    queryFn: () => listRunningTasks(),
    refetchInterval: (query) => {
      const rows = query.state.data;
      return Array.isArray(rows) && rows.length > 0 ? 4000 : false;
    },
  });
}

// v2.4.0 — Status-tab attribution table. Data is small (one row per
// distinct user/agent/model tuple for the engagement) so we don't
// bother with polling — window-focus refresh is enough.
export function useEngagementAttribution(slug: string) {
  return useQuery({
    queryKey: qk.engagementAttribution(slug),
    queryFn: () => listEngagementAttribution(slug),
    enabled: Boolean(slug),
  });
}

// v2.5.0 — Analytics page hooks. All accept `engagement` as null | "all"
// | <slug>. Window-focus revalidate; no polling.
// v2.5.2 — findings-over-time takes an opts bag so the panel can pick
// day/week/month/custom buckets.
export function useAnalyticsFindingsOverTime(
  engagement: string | null,
  opts: import("@/lib/api").FindingsOverTimeOpts = {},
) {
  return useQuery({
    queryKey: qk.analyticsFindingsOverTime(engagement, opts),
    queryFn: () => fetchFindingsOverTime(engagement, opts),
  });
}
export function useAnalyticsSeverityBreakdown(engagement: string | null) {
  return useQuery({
    queryKey: qk.analyticsSeverityBreakdown(engagement),
    queryFn: () => fetchSeverityBreakdown(engagement),
  });
}
export function useAnalyticsScanCoverage(engagement: string | null) {
  return useQuery({
    queryKey: qk.analyticsScanCoverage(engagement),
    queryFn: () => fetchScanCoverage(engagement),
  });
}
export function useAnalyticsTopFindings(engagement: string | null, limit = 3) {
  return useQuery({
    queryKey: qk.analyticsTopFindings(engagement, limit),
    queryFn: () => fetchTopFindings(engagement, limit),
  });
}
export function useAnalyticsEngagementLog(
  engagement: string | null,
  limit = 100,
) {
  return useQuery({
    queryKey: qk.analyticsEngagementLog(engagement, limit),
    queryFn: () => fetchEngagementLog(engagement, limit),
  });
}

// v0.21.0 (finding pane): single-finding + activity timeline.
export function useFinding(findingId: string) {
  return useQuery({
    queryKey: qk.finding(findingId),
    queryFn: () => getFinding(findingId),
  });
}

export function useFindingActivity(findingId: string) {
  return useQuery({
    queryKey: qk.findingActivity(findingId),
    queryFn: () => getFindingActivity(findingId),
  });
}

export function useFindingChat(findingId: string) {
  return useQuery({
    queryKey: qk.findingChat(findingId),
    queryFn: () => getFindingChat(findingId),
  });
}

export function useAskFindingChatMutation(findingId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { message: string; conversation_id?: string | null }) =>
      askFindingChat(findingId, body),
    onSuccess: (resp: FindingChatResponse) => {
      qc.setQueryData<FindingChatState>(qk.findingChat(findingId), (prev) => ({
        conversation_id: resp.conversation_id,
        messages: [
          ...(prev?.messages ?? []),
          resp.user_message,
          resp.assistant_message,
        ],
      }));
      qc.invalidateQueries({ queryKey: qk.findingChat(findingId) });
      qc.invalidateQueries({ queryKey: qk.findingActivity(findingId) });
    },
  });
}

export function useClearFindingChatMutation(findingId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => clearFindingChat(findingId),
    onSuccess: () => {
      qc.setQueryData<FindingChatState>(qk.findingChat(findingId), {
        conversation_id: null,
        messages: [],
      });
      qc.invalidateQueries({ queryKey: qk.findingActivity(findingId) });
    },
  });
}

export function useSummarizeFindingChatMutation(findingId: string) {
  // Summarize the conversation into a reviewable activity entry, then clear.
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => summarizeFindingChat(findingId),
    onSuccess: async () => {
      qc.invalidateQueries({ queryKey: qk.findingActivity(findingId) });
      await clearFindingChat(findingId);
      qc.setQueryData<FindingChatState>(qk.findingChat(findingId), {
        conversation_id: null,
        messages: [],
      });
    },
  });
}

export function useAcceptFindingChatActionMutation(findingId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { messageId: string; actionIndex: number }) =>
      acceptFindingChatAction(findingId, body.messageId, body.actionIndex),
    onSuccess: (resp: FindingChatActionResponse) => {
      qc.setQueryData<FindingChatState>(qk.findingChat(findingId), (prev) => ({
        conversation_id: prev?.conversation_id ?? resp.message.conversation_id,
        messages: (prev?.messages ?? []).map((m) =>
          m.id === resp.message.id ? resp.message : m,
        ),
      }));
      qc.invalidateQueries({ queryKey: qk.finding(findingId) });
      qc.invalidateQueries({ queryKey: qk.findingActivity(findingId) });
      qc.invalidateQueries({ queryKey: qk.findingChat(findingId) });
    },
  });
}

export function useDenyFindingChatActionMutation(findingId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { messageId: string; actionIndex: number }) =>
      denyFindingChatAction(findingId, body.messageId, body.actionIndex),
    onSuccess: (resp: FindingChatActionResponse) => {
      qc.setQueryData<FindingChatState>(qk.findingChat(findingId), (prev) => ({
        conversation_id: prev?.conversation_id ?? resp.message.conversation_id,
        messages: (prev?.messages ?? []).map((m) =>
          m.id === resp.message.id ? resp.message : m,
        ),
      }));
      qc.invalidateQueries({ queryKey: qk.findingChat(findingId) });
    },
  });
}

// Helpers exported for the SSE handler + mutation onSuccess bodies.
// Callers pass a QueryClient (from useQueryClient()) — encapsulating
// the setQueryData shape here keeps the "how" of merging out of the
// call site.
type QC = { setQueryData: <T>(k: readonly unknown[], up: (prev: T | undefined) => T) => void };

export function upsertFindingInCache(
  qc: QC,
  slug: string,
  finding: Finding,
) {
  qc.setQueryData<Finding[]>(qk.findings(slug), (prev) => {
    if (!prev) return [finding];
    const idx = prev.findIndex((f) => f.id === finding.id);
    if (idx === -1) return [finding, ...prev];
    const next = prev.slice();
    next[idx] = finding;
    return next;
  });
}

export function removeFindingFromCache(
  qc: QC,
  slug: string,
  findingId: string,
) {
  qc.setQueryData<Finding[]>(qk.findings(slug), (prev) =>
    prev ? prev.filter((f) => f.id !== findingId) : [],
  );
}

export function useArchiveEngagementMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => archiveEngagement(slug),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.engagement(slug) }),
  });
}

export function useUpdateEngagementMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      auto_assess_enabled?: boolean;
      name?: string;
      status?: import("@/lib/types").EngagementStatus;
    }) => updateEngagement(slug, body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.engagement(slug) }),
  });
}

export function useFlushEngagementMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => flushEngagement(slug),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.engagements() }),
  });
}

// ── observations ─────────────────────────────────────────────────────

export function useObservations(slug: string) {
  return useQuery({
    queryKey: qk.observations(slug),
    queryFn: () => listObservations(slug),
  });
}

export function useCreateObservationMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof createObservation>[1]) =>
      createObservation(slug, body),
    onSuccess: (created) =>
      qc.setQueryData<Observation[]>(qk.observations(slug), (prev) =>
        prev ? [...prev, created] : [created],
      ),
  });
}

export function useDeleteObservationMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteObservation(id),
    onSuccess: (_res, id) =>
      qc.setQueryData<Observation[]>(qk.observations(slug), (prev) =>
        prev ? prev.filter((o) => o.id !== id) : [],
      ),
  });
}

// v1.4.8: observation ↔ finding links. The mutation optimistically patches
// the observation's finding_ids in the cache and also invalidates the
// findings cache so the finding slide-over's back-ref stays fresh.
export function useLinkObservationFindingMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ([obsId, findingId]: [string, string]) =>
      linkObservationFinding(obsId, findingId),
    onSuccess: (updated, [obsId]) => {
      qc.setQueryData<Observation[]>(qk.observations(slug), (prev) =>
        prev
          ? prev.map((o) => (o.id === obsId ? { ...o, ...updated } : o))
          : [updated],
      );
      qc.invalidateQueries({ queryKey: qk.findings(slug) });
    },
  });
}

export function useUnlinkObservationFindingMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ([obsId, findingId]: [string, string]) =>
      unlinkObservationFinding(obsId, findingId),
    onSuccess: (_res, [obsId, findingId]) => {
      qc.setQueryData<Observation[]>(qk.observations(slug), (prev) =>
        prev
          ? prev.map((o) =>
              o.id === obsId
                ? {
                    ...o,
                    finding_ids: (o.finding_ids ?? []).filter(
                      (fid) => fid !== findingId,
                    ),
                  }
                : o,
            )
          : [],
      );
      qc.invalidateQueries({ queryKey: qk.findings(slug) });
    },
  });
}

// ── scope ────────────────────────────────────────────────────────────

export function useScope(slug: string) {
  return useQuery({
    queryKey: qk.scope(slug),
    queryFn: () => listScope(slug),
  });
}

export function useCreateScopeItemMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof createScopeItem>[1]) =>
      createScopeItem(slug, body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.scope(slug) }),
  });
}

export function useDeleteScopeItemMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteScopeItem(slug, id),
    onSuccess: (_res, id) =>
      qc.setQueryData<ScopeItem[]>(qk.scope(slug), (prev) =>
        prev ? prev.filter((s) => s.id !== id) : [],
      ),
  });
}

// ── entities ─────────────────────────────────────────────────────────

export function useEntities(slug: string) {
  return useQuery({
    queryKey: qk.entities(slug),
    queryFn: () => listEntities(slug),
  });
}

export function useStoredEntities(slug: string, includeSuppressed = false) {
  return useQuery({
    queryKey: qk.storedEntities(slug, includeSuppressed),
    queryFn: () => listStoredEntities(slug, includeSuppressed),
  });
}

export function useEntityDuplicateCandidates(slug: string) {
  return useQuery({
    queryKey: qk.entityDuplicateCandidates(slug),
    queryFn: () => listEntityDuplicateCandidates(slug),
  });
}

export function useFindingContext(findingId: string) {
  return useQuery({
    queryKey: qk.findingContext(findingId),
    queryFn: () => listFindingContextCandidates(findingId),
  });
}

export function usePromoteFindingContextMutation(findingId: string, slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (items: Parameters<typeof promoteFindingContext>[1]) =>
      promoteFindingContext(findingId, items),
    onSuccess: (result) => {
      qc.setQueryData(qk.findingContext(findingId), result.candidates);
      void qc.invalidateQueries({ queryKey: qk.scope(slug) });
      void qc.invalidateQueries({ queryKey: qk.entities(slug) });
      void qc.invalidateQueries({ queryKey: qk.storedEntities(slug) });
    },
  });
}

// ── contributions ────────────────────────────────────────────────────

export function useContributionsHeatmap(
  slug: string,
  filters: { actorId: string | null; source: ContributionSource | null },
) {
  return useQuery({
    queryKey: qk.contributionsHeatmap(slug, filters),
    queryFn: () =>
      getContributionsHeatmap(slug, {
        actorId: filters.actorId,
        source: filters.source,
      }),
  });
}

export function useContributionsEntries(
  slug: string,
  filters: {
    date: string;
    actorId: string | null;
    source: ContributionSource | null;
  },
) {
  return useQuery({
    queryKey: qk.contributionsEntries(slug, filters),
    queryFn: () =>
      getContributionsEntries(slug, {
        date: filters.date,
        actorId: filters.actorId,
        source: filters.source,
        limit: 200,
      }),
  });
}

// ── me / whoami ──────────────────────────────────────────────────────
// Shared across every settings page + IdentityMenu. One cache = one
// network round-trip per session (staleTime carries it further).

export function useMe() {
  return useQuery({
    queryKey: qk.me(),
    queryFn: getMe,
    staleTime: 5 * 60_000,
  });
}

// v1.4.11: persist the analyst's default model; patches the /me cache.
export function useUpdateMyPreferencesMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof updateMyPreferences>[0]) =>
      updateMyPreferences(body),
    onSuccess: (updated) => qc.setQueryData(qk.me(), updated),
  });
}

// ── release notes ────────────────────────────────────────────────────

export function useReleases() {
  return useQuery({
    queryKey: qk.releases(),
    queryFn: () => loadReleases(),
    staleTime: 60 * 60_000,
  });
}

// ── provider keys ────────────────────────────────────────────────────

export function useProviderKeys() {
  return useQuery({
    queryKey: qk.providerKeys(),
    queryFn: () => listProviderKeys(),
  });
}

export function useDeleteProviderKeyMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keyId: string) => deleteProviderKey(keyId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.providerKeys() }),
  });
}

// ── v1.24.0 Settings > Configurations ────────────────────────────────

export function useAgentConfigurations() {
  return useQuery({
    queryKey: qk.agentConfigurations(),
    queryFn: () => listAgentConfigurations(),
  });
}

export function usePutAgentConfigurationMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, body }: { slug: string; body: AgentConfigPut }) =>
      putAgentConfiguration(slug, body),
    onSuccess: (data: AgentConfigRead) => {
      // Optimistic replace inside the list cache so the picker updates
      // without waiting on the refetch.
      qc.setQueryData(qk.agentConfigurations(), (prev: unknown) => {
        const list = (prev as { configurations?: AgentConfigRead[] })
          ?.configurations;
        if (!Array.isArray(list)) return prev;
        const next = list.filter(
          (c) => c.engagement_slug !== data.engagement_slug,
        );
        // Empty rows (all configurable roles null) are dropped from the list.
        if (
          data.strategic ||
          data.engagement_strategist ||
          data.tactical ||
          data.correlate
        ) {
          next.push(data);
          next.sort((a, b) =>
            a.engagement_slug.localeCompare(b.engagement_slug),
          );
        }
        return { configurations: next };
      });
      qc.invalidateQueries({ queryKey: qk.agentConfigurations() });
    },
  });
}

export function useClearAgentConfigurationMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => clearAgentConfiguration(slug),
    onSuccess: (_data, slug) => {
      qc.setQueryData(qk.agentConfigurations(), (prev: unknown) => {
        const list = (prev as { configurations?: AgentConfigRead[] })
          ?.configurations;
        if (!Array.isArray(list)) return prev;
        return {
          configurations: list.filter((c) => c.engagement_slug !== slug),
        };
      });
      qc.invalidateQueries({ queryKey: qk.agentConfigurations() });
    },
  });
}

export function useDownloadAgentConfigurations() {
  return useMutation({
    mutationFn: () => downloadAgentConfigurations(),
  });
}

export function useImportAgentConfigurationsMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AgentConfigExport): Promise<AgentConfigImportResult> =>
      importAgentConfigurations(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.agentConfigurations() });
    },
  });
}

// ── admin users / RBAC ───────────────────────────────────────────────

export function useAdminUsers() {
  return useQuery({
    queryKey: qk.adminUsers(),
    queryFn: () => listAdminUsers(),
  });
}

export function useUpdateUserRoleMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: { userId: string; role: UserRole }) =>
      updateUserRole(params.userId, params.role),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.adminUsers() }),
  });
}

export function useUpdateUserActiveMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: { userId: string; is_active: boolean }) =>
      updateUserActive(params.userId, params.is_active),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.adminUsers() }),
  });
}

// ── integrations ─────────────────────────────────────────────────────

export function useIntegrations() {
  return useQuery({
    queryKey: qk.integrations(),
    queryFn: () => listIntegrations(),
  });
}

export function useCreateIntegrationMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof createIntegration>[0]) =>
      createIntegration(body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.integrations() }),
  });
}

export function useUpdateIntegrationMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      integrationId: string;
      body: Parameters<typeof updateIntegration>[1];
    }) => updateIntegration(params.integrationId, params.body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.integrations() }),
  });
}

export function useDeleteIntegrationMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteIntegration(id),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.integrations() }),
  });
}

// ── roadmap suggestions (feedback tab) ───────────────────────────────

export function useRoadmapSuggestions(
  filters: RoadmapListFilters | undefined,
) {
  return useQuery({
    queryKey: qk.roadmapSuggestions(filters),
    queryFn: () => listRoadmapSuggestions(filters),
  });
}

// ── authorizations / grants ──────────────────────────────────────────

export function useAuthorizations(
  engagementId: string,
  active?: boolean,
) {
  return useQuery({
    queryKey: qk.authorizations(engagementId, active),
    queryFn: () => listAuthorizations(engagementId, active),
    enabled: Boolean(engagementId),
  });
}

export function useRevokeAuthorizationMutation(engagementId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (authorizationId: string) =>
      revokeAuthorization(authorizationId),
    onSuccess: () =>
      // Invalidate all authorization variants for this engagement (active,
      // inactive, undefined) — the shape of the tuple key doesn't matter
      // for prefix match.
      qc.invalidateQueries({
        queryKey: ["authorizations", engagementId],
      }),
  });
}

// ── approvals ────────────────────────────────────────────────────────

export function usePendingApprovals() {
  return useQuery({
    queryKey: qk.pendingApprovals(),
    queryFn: listPendingApprovals,
    refetchInterval: (query) =>
      (query.state.data?.length ?? 0) > 0 ? 3_000 : 10_000,
  });
}

export function useDecideApprovalMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      approvalId: string;
      body: Parameters<typeof decideApproval>[1];
    }) => decideApproval(params.approvalId, params.body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.pendingApprovals() });
      void qc.invalidateQueries({ queryKey: ["engagement-status"] });
    },
  });
}

// ── admin tools catalog (settings) ───────────────────────────────────

export function useApproveToolMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      toolId: string;
      opts?: Parameters<typeof approveTool>[1];
    }) => approveTool(params.toolId, params.opts),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tools"] }),
  });
}

export function useRevokeToolMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (toolId: string) => revokeTool(toolId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tools"] }),
  });
}

export function useDeleteToolMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (toolId: string) => deleteTool(toolId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tools"] }),
  });
}

// Re-export Integration type only if callers pass Integration through
// mutation args — kept to satisfy TS narrowing at edit sites.
export type { Integration };

// ── prefetch helpers ─────────────────────────────────────────────────
// v1.0.0(4b): warm the query cache on nav hover. By the time the analyst
// clicks the target route the fetch is already in flight (or done), so the
// route swap paints from cache with no loading spinner.
//
// Not every view gets a prefetch entry — Contributions is skipped because
// its query key depends on filter chips (date + actorId + source) that the
// analyst hasn't picked yet at hover time. Report is skipped because it
// has no fetch. Scope's shape is fine.

import type { QueryClient } from "@tanstack/react-query";
import type { EngagementView } from "@/components/engagement-nav";

export function prefetchEngagementView(
  qc: QueryClient,
  slug: string,
  view: EngagementView,
): void {
  switch (view) {
    case "findings":
      void qc.prefetchQuery({
        queryKey: qk.findings(slug),
        queryFn: () => listFindings(slug),
      });
      return;
    case "entities":
      void qc.prefetchQuery({
        queryKey: qk.entities(slug),
        queryFn: () => listEntities(slug),
      });
      void qc.prefetchQuery({
        queryKey: qk.storedEntities(slug),
        queryFn: () => listStoredEntities(slug),
      });
      return;
    case "observations":
      void qc.prefetchQuery({
        queryKey: qk.observations(slug),
        queryFn: () => listObservations(slug),
      });
      return;
    case "scope":
      void qc.prefetchQuery({
        queryKey: qk.scope(slug),
        queryFn: () => listScope(slug),
      });
      return;
    case "status":
      void qc.prefetchQuery({
        queryKey: qk.engagementStatus(slug),
        queryFn: () => getEngagementStatus(slug),
      });
      return;
    case "costs":
      void qc.prefetchQuery({
        queryKey: qk.engagementCosts(slug),
        queryFn: () => getEngagementCosts(slug),
      });
      return;
    case "strategy":
    case "contributions":
      // Strategy loads a coordinated dossier; contributions depends on filter
      // state.
      return;
  }
}

export function useEngagementStatus(slug: string) {
  // Was: 2s setInterval in StatusView. Same cadence — the analyst
  // explicitly wanted this fast when an agent is running (v0.8.1
  // regression fix). refetchOnWindowFocus is inherited from the root
  // QueryClient, so tab-focus also refreshes.
  return useQuery({
    queryKey: qk.engagementStatus(slug),
    queryFn: () => getEngagementStatus(slug),
    refetchInterval: 2_000,
  });
}

// v1.2.0: lazy step-log fetch. ``enabled`` gates the request so the
// query only fires when the Expand modal actually opens. Poll every
// 3s while the entity is non-terminal so live tool calls / findings
// stream in — the caller passes ``liveTerminal`` to switch that off
// once the entity has reached a final colour.
export function useStatusSteps(
  slug: string,
  kind: StatusKind | null,
  entityId: string | null,
  opts: { liveTerminal?: boolean } = {},
) {
  const enabled = kind !== null && entityId !== null;
  return useQuery({
    queryKey: enabled
      ? qk.statusSteps(slug, kind as string, entityId as string)
      : ["status-steps-disabled"],
    queryFn: () =>
      getStatusSteps(slug, kind as StatusKind, entityId as string),
    enabled,
    refetchInterval: opts.liveTerminal ? false : 3_000,
  });
}

// v1.2.0: tenant-global runs — planner rank/combine/re-evaluate.
export function useGlobalAgentRuns() {
  return useQuery({
    queryKey: qk.globalAgentRuns(),
    queryFn: () => listGlobalAgentRuns(),
    refetchInterval: 4_000,
  });
}

export function useGlobalAgentRunSteps(
  executionId: string | null,
  opts: { liveTerminal?: boolean } = {},
) {
  const enabled = executionId !== null;
  return useQuery({
    queryKey: enabled
      ? qk.globalAgentRunSteps(executionId as string)
      : ["global-agent-run-steps-disabled"],
    queryFn: () => getGlobalAgentRunSteps(executionId as string),
    enabled,
    refetchInterval: opts.liveTerminal ? false : 3_000,
  });
}

export function useReportReadiness(slug: string) {
  return useQuery({
    queryKey: qk.reportReadiness(slug),
    queryFn: () => getReportReadiness(slug),
  });
}

export function useEngagementCosts(slug: string) {
  // Costs are cheap to recompute but not real-time. 15s polling covers
  // the "run an agent, see the tokens land" flow without hammering the
  // rollup query.
  return useQuery({
    queryKey: qk.engagementCosts(slug),
    queryFn: () => getEngagementCosts(slug),
    refetchInterval: 15_000,
  });
}

export function useTools(
  opts: { status?: ToolStatus; first_party?: boolean } = {},
) {
  return useQuery({
    queryKey: qk.tools(opts),
    queryFn: () => listTools(opts),
  });
}

// v1.12.0: default tools = the orchestrator's built-in MCP tools
// (subfinder, dns_lookup, port_scan, etc.). Shared by the Settings >
// Tools tab banner and the Scope-tab "Current Tools" panel.
//
// v1.11.0 pointed at ``useTools({first_party:true})`` — the analyst-
// upload catalog filtered by ``created_by_user_id IS NULL``. That
// table is empty on a fresh install, so the banner rendered as "no
// tools registered." The actual defaults live in the orchestrator's
// FastMCP registry (see backend/app/api/orchestrator_tools.py).
export function useDefaultTools() {
  return useQuery({
    queryKey: qk.orchestratorTools(),
    queryFn: () => listOrchestratorTools(),
  });
}

export function useToolInvocations(slug: string) {
  // Only poll while there are queued/running rows. Once everything is
  // terminal, the interval switches off and we rely on window-focus
  // revalidation + explicit invalidation on new invokes.
  return useQuery({
    queryKey: qk.toolInvocations(slug),
    queryFn: () => listToolInvocations(slug),
    refetchInterval: (query) => {
      const rows = query.state.data as
        | ToolInvocationRead[]
        | undefined;
      const active = rows?.some(
        (r) => r.status === "queued" || r.status === "running",
      );
      return active ? 3_000 : false;
    },
  });
}

export function useRetryTaskMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: retryTask,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.engagementStatus(slug) }),
  });
}

export function useCancelTaskMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: cancelTask,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.engagementStatus(slug) }),
  });
}

export function useCancelAgentExecutionMutation(slug?: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: cancelAgentExecution,
    onSuccess: () => {
      if (slug) {
        qc.invalidateQueries({ queryKey: qk.engagementStatus(slug) });
      } else {
        qc.invalidateQueries({ queryKey: qk.globalAgentRuns() });
      }
    },
  });
}

export function useRetryAgentExecutionMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: retryAgentExecution,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.engagementStatus(slug) }),
  });
}

// ── v2.10.0 Infrastructure tab ───────────────────────────────────────
// 15s refetchInterval so start/stop transitions surface without a
// manual refresh — Azure LROs typically settle in <1 min.

export function useInfraStatus(opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.infraStatus(),
    queryFn: getInfraStatus,
    enabled: opts.enabled ?? true,
    staleTime: 5 * 60_000,
  });
}

export function useInfraSubscriptions(opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.infraSubscriptions(),
    queryFn: listInfraSubscriptions,
    enabled: opts.enabled ?? true,
    staleTime: 5 * 60_000,
  });
}

export function useVms(opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.vms(),
    queryFn: listVms,
    enabled: opts.enabled ?? true,
    refetchInterval: 15_000,
  });
}

export function useStartVmMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (armId: string) => startVm(armId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.vms() }),
  });
}

export function useDeallocateVmMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (armId: string) => deallocateVm(armId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.vms() }),
  });
}

export function useRestartVmMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (armId: string) => restartVm(armId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.vms() }),
  });
}

export function useAutoShutdown(armId: string, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.autoShutdown(armId),
    queryFn: () => getAutoShutdown(armId),
    enabled: opts.enabled ?? true,
  });
}

export function usePutAutoShutdownMutation(armId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: import("@/lib/types").AutoShutdownWrite) =>
      putAutoShutdown(armId, body),
    onSuccess: (fresh) => qc.setQueryData(qk.autoShutdown(armId), fresh),
  });
}

export function useDeleteAutoShutdownMutation(armId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteAutoShutdown(armId),
    onSuccess: () => qc.setQueryData(qk.autoShutdown(armId), null),
  });
}

export function useRunCommandMutation(armId: string) {
  return useMutation({
    mutationFn: (script: string) => runCommand(armId, script),
  });
}
