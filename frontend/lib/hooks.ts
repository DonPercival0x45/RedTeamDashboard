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
  getEngagementCosts,
  getEngagementStatus,
  listToolInvocations,
  listTools,
  retryAgentExecution,
  retryTask,
} from "@/lib/api";
import type { ToolInvocationRead, ToolStatus } from "@/lib/types";

export const qk = {
  engagementStatus: (slug: string) =>
    ["engagement-status", slug] as const,
  engagementCosts: (slug: string) =>
    ["engagement-costs", slug] as const,
  tools: (opts: { status?: ToolStatus }) => ["tools", opts] as const,
  toolInvocations: (slug: string) =>
    ["tool-invocations", slug] as const,
};

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
