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
  archiveEngagement,
  createObservation,
  createScopeItem,
  deleteObservation,
  deleteScopeItem,
  flushEngagement,
  getContributionsEntries,
  getContributionsHeatmap,
  getEngagement,
  getEngagementCosts,
  getEngagementStatus,
  listEngagements,
  listEntities,
  listFindings,
  listObservations,
  listScope,
  listStoredEntities,
  listToolInvocations,
  listTools,
  retryAgentExecution,
  retryTask,
} from "@/lib/api";
import type {
  ContributionSource,
  Finding,
  Observation,
  ScopeItem,
  ToolInvocationRead,
  ToolStatus,
} from "@/lib/types";

export const qk = {
  engagements: () => ["engagements"] as const,
  engagement: (slug: string) => ["engagement", slug] as const,
  findings: (slug: string) => ["findings", slug] as const,
  observations: (slug: string) => ["observations", slug] as const,
  scope: (slug: string) => ["scope", slug] as const,
  entities: (slug: string) => ["entities", slug] as const,
  storedEntities: (slug: string) => ["stored-entities", slug] as const,
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
  engagementCosts: (slug: string) =>
    ["engagement-costs", slug] as const,
  tools: (opts: { status?: ToolStatus }) => ["tools", opts] as const,
  toolInvocations: (slug: string) =>
    ["tool-invocations", slug] as const,
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

export function useFindings(slug: string) {
  // Findings are cached across route changes so navigating away and back
  // is instant. The SSE stream in app/e/page.tsx merges new findings
  // into this cache via qc.setQueryData(qk.findings(slug), ...).
  return useQuery({
    queryKey: qk.findings(slug),
    queryFn: () => listFindings(slug),
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

export function useStoredEntities(slug: string) {
  return useQuery({
    queryKey: qk.storedEntities(slug),
    queryFn: () => listStoredEntities(slug),
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

export function useTools(opts: { status?: ToolStatus } = {}) {
  return useQuery({
    queryKey: qk.tools(opts),
    queryFn: () => listTools(opts),
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

export function useRetryAgentExecutionMutation(slug: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: retryAgentExecution,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.engagementStatus(slug) }),
  });
}
