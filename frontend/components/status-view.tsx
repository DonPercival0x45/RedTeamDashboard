"use client";

// Status tab (v0.8.0+). Per-engagement timeline of LLM agent calls,
// orchestrator tasks, and approval gates. Color-coded per the brief:
//
//   green   active     — currently running / in flight
//   blue    pending    — queued, awaiting action
//   purple  completed  — terminal success
//   red     failed     — terminal failure (retry button shows here)
//
// v0.8.2: every entity exposes a `history` timeline (active → completed,
// pending → dispatched → completed, etc.) rendered at the top of the
// Expand modal so the analyst can see how the box reached its current
// colour. The old standalone Event log moved here as a collapsible
// "Live events" panel below the boxes.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleSlash,
  Clock,
  LayoutGrid,
  List,
  RefreshCcw,
  Search,
  Slash,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { AttributionTable } from "@/components/attribution-table";
import { DateTime } from "@/components/date-time";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useCancelAgentExecutionMutation,
  useCancelTaskMutation,
  useEngagementStatus,
  useRetryAgentExecutionMutation,
  useRetryTaskMutation,
  useStatusSteps,
} from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type {
  LoggedEvent,
  RunEvent,
  StatusColor,
  StatusEntity,
  StatusKind,
  StatusOutcome,
  StatusTransition,
} from "@/lib/types";

const COLOR_BADGE: Record<StatusColor, string> = {
  active:
    "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
  pending: "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-200",
  completed:
    "border-violet-500/50 bg-violet-500/10 text-violet-700 dark:text-violet-200",
  failed: "border-rose-500/50 bg-rose-500/10 text-rose-700 dark:text-rose-200",
};

const COLOR_BORDER: Record<StatusColor, string> = {
  active: "border-emerald-500/40 hover:border-emerald-500/70",
  pending: "border-sky-500/40 hover:border-sky-500/70",
  completed: "border-violet-500/40 hover:border-violet-500/70",
  failed: "border-rose-500/50 hover:border-rose-500/80",
};

const COLOR_LABEL: Record<StatusColor, string> = {
  active: "Active",
  pending: "Pending",
  completed: "Complete",
  failed: "Failed",
};

const COLOR_ICON: Record<StatusColor, LucideIcon> = {
  active: Activity,
  pending: Clock,
  completed: CheckCircle2,
  failed: XCircle,
};

const KIND_LABEL: Record<StatusKind, string> = {
  agent: "Agent",
  task: "Task",
  approval: "Approval",
};

const KIND_FILTERS: (StatusKind | "all")[] = [
  "all",
  "agent",
  "task",
  "approval",
];

// v1.2.0 outcome sub-badges under the four colours. Only render when
// the entity has reached a terminal state (color in completed/failed).
const OUTCOME_LABEL: Record<StatusOutcome, string> = {
  success: "Success",
  empty: "Empty",
  partial: "Partial",
  errored: "Errored",
};

const OUTCOME_ICON: Record<StatusOutcome, LucideIcon> = {
  success: CheckCircle2,
  empty: CircleSlash,
  partial: Slash,
  errored: XCircle,
};

const OUTCOME_CLASS: Record<StatusOutcome, string> = {
  success: "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
  empty: "border-slate-500/50 bg-slate-500/10 text-slate-700 dark:text-slate-200",
  partial: "border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-200",
  errored: "border-rose-500/50 bg-rose-500/10 text-rose-700 dark:text-rose-200",
};

// v1.2.0 date-range chips over started_at. "all" is the default.
type DateRange = "all" | "24h" | "7d" | "14d" | "30d";
type ViewMode = "cards" | "table";
const DATE_RANGE_MS: Record<Exclude<DateRange, "all">, number> = {
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "14d": 14 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
};
const DATE_RANGE_FILTERS: DateRange[] = ["all", "24h", "7d", "14d", "30d"];

// v0.8.2: Live events log helpers — used to lifted from event-log.tsx.

export const EVENT_COLORS: Record<RunEvent["type"], string> = {
  "run.started": "border-sky-500 text-sky-700 dark:text-sky-200",
  "approval.pending": "border-amber-500 text-amber-700 dark:text-amber-200",
  "tool.denied": "border-orange-500 text-orange-700 dark:text-orange-200",
  "tool.auto_approved": "border-violet-500 text-violet-700 dark:text-violet-200",
  "finding.created": "border-emerald-500 text-emerald-700 dark:text-emerald-200",
  "run.completed": "border-zinc-500 text-zinc-600 dark:text-zinc-300",
  "run.errored": "border-rose-500 text-rose-700 dark:text-rose-200",
};

export function summarizeEvent(event: RunEvent): string {
  switch (event.type) {
    case "run.started":
      return event.prompt;
    case "approval.pending":
      return `${event.tool} (${event.risk}) — ${JSON.stringify(event.args)}`;
    case "tool.denied":
      return `${event.tool} ${JSON.stringify(event.args)} — ${event.reason}`;
    case "tool.auto_approved":
      return `${event.tool} ${JSON.stringify(event.args)} — auto-approved (session grant)`;
    case "finding.created":
      return `${event.tool} → ${JSON.stringify(event.data).slice(0, 140)}`;
    case "run.completed":
      return `thread ${event.thread_id.slice(0, 8)}…`;
    case "run.errored":
      return event.error;
  }
}

export function StatusView({
  slug,
}: {
  slug: string;
}) {
  // v1.0.0: react-query owns the fetch + 2s polling + focus revalidation.
  // The old useEffect + setInterval + manual reload is gone; the useQuery
  // hook in lib/hooks.ts sets refetchInterval: 2_000 and inherits
  // refetchOnWindowFocus from the root QueryClient.
  const {
    data,
    error: queryError,
    refetch,
  } = useEngagementStatus(slug);
  const retryTaskMutation = useRetryTaskMutation(slug);
  const retryAgentMutation = useRetryAgentExecutionMutation(slug);
  const cancelTaskMutation = useCancelTaskMutation(slug);
  const cancelAgentMutation = useCancelAgentExecutionMutation(slug);

  const [localError, setLocalError] = useState<string | null>(null);
  const error = localError ?? (queryError instanceof Error ? queryError.message : queryError ? String(queryError) : null);

  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const runParam = searchParams?.get("run") ?? null;
  const initialKind = searchParams?.get("statusKind");
  const initialColor = searchParams?.get("statusColor");
  const initialOutcome = searchParams?.get("statusOutcome");
  const initialAgent = searchParams?.get("statusAgent") ?? "all";
  const initialRange = searchParams?.get("statusRange");
  const initialSearch = searchParams?.get("statusSearch") ?? "";
  const initialView = searchParams?.get("statusView");
  const [filter, setFilter] = useState<StatusKind | "all">(
    initialKind && ["agent", "task", "approval"].includes(initialKind)
      ? (initialKind as StatusKind)
      : "all",
  );
  const [colorFilter, setColorFilter] = useState<StatusColor | "all">(
    initialColor && ["active", "pending", "completed", "failed"].includes(initialColor)
      ? (initialColor as StatusColor)
      : "all",
  );
  const [outcomeFilter, setOutcomeFilter] = useState<StatusOutcome | "all">(
    initialOutcome && ["success", "empty", "partial", "errored"].includes(initialOutcome)
      ? (initialOutcome as StatusOutcome)
      : "all",
  );
  const [agentFilter, setAgentFilter] = useState(initialAgent);
  const [viewMode, setViewMode] = useState<ViewMode>(initialView === "table" ? "table" : "cards");
  const [expanded, setExpanded] = useState<StatusEntity | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [bulkCancelling, setBulkCancelling] = useState(false);
  // v1.2.0 — typed search over title / subtitle / synopsis / run_slug,
  // plus a date-range chip over started_at.
  const [search, setSearch] = useState(initialSearch);
  // v2.5.1: show the first N runs on Status entry so the Attribution
  // section below is visible without a long scroll. Expanding switches
  // to a bounded scroll container that shows the full filtered list.
  const [showAllRuns, setShowAllRuns] = useState(false);
  const [dateRange, setDateRange] = useState<DateRange>(
    initialRange && ["24h", "7d", "14d", "30d"].includes(initialRange)
      ? (initialRange as DateRange)
      : "all",
  );
  const onRetry = useCallback(
    async (entity: StatusEntity) => {
      setRetryingId(entity.id);
      setLocalError(null);
      try {
        if (entity.kind === "task") {
          await retryTaskMutation.mutateAsync(entity.id);
        } else if (entity.kind === "agent") {
          await retryAgentMutation.mutateAsync(entity.id);
        } else {
          return;
        }
      } catch (err) {
        setLocalError(err instanceof Error ? err.message : String(err));
      } finally {
        setRetryingId(null);
      }
    },
    [retryTaskMutation, retryAgentMutation],
  );

  const onCancel = useCallback(
    async (entity: StatusEntity) => {
      setCancellingId(entity.id);
      setLocalError(null);
      try {
        if (entity.kind === "task") {
          await cancelTaskMutation.mutateAsync(entity.id);
        } else if (entity.kind === "agent" && entity.raw_status === "running") {
          await cancelAgentMutation.mutateAsync(entity.id);
        } else {
          return;
        }
      } catch (err) {
        setLocalError(err instanceof Error ? err.message : String(err));
      } finally {
        setCancellingId(null);
      }
    },
    [cancelTaskMutation, cancelAgentMutation],
  );

  const all = useMemo<StatusEntity[]>(
    () => data
      ? [...data.agents, ...data.tasks, ...data.approvals].sort((a, b) => {
          const ta = (a.started_at || a.completed_at || "").toString();
          const tb = (b.started_at || b.completed_at || "").toString();
          return tb.localeCompare(ta);
        })
      : [],
    [data],
  );

  const agentRoles = useMemo(
    () => Array.from(new Set(
      all
        .filter((entity) => entity.kind === "agent")
        .map((entity) => String(entity.log.agent ?? "unknown")),
    )).sort(),
    [all],
  );

  useEffect(() => {
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    const write = (key: string, value: string, fallback: string) => {
      if (value === fallback) params.delete(key); else params.set(key, value);
    };
    write("statusKind", filter, "all");
    write("statusColor", colorFilter, "all");
    write("statusOutcome", outcomeFilter, "all");
    write("statusAgent", agentFilter, "all");
    write("statusRange", dateRange, "all");
    write("statusSearch", search, "");
    write("statusView", viewMode, "cards");
    const query = params.toString();
    if (query === (searchParams?.toString() ?? "")) return;
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }, [
    agentFilter,
    colorFilter,
    dateRange,
    filter,
    outcomeFilter,
    pathname,
    router,
    search,
    searchParams,
    viewMode,
  ]);

  // v1.2.0: deep-link. When the URL carries ``?run=<id>`` (from a
  // kickoff toast anywhere in the app), auto-open Expand on the
  // matching entity as soon as the feed loads. Match is:
  //   - exact entity id (task / agent execution / approval)
  //   - OR thread_id / run_id prefix (start-run toast uses thread_id;
  //     the entity id and thread_id differ but both share a UUID
  //     prefix on the same run — a startsWith match catches either).
  useEffect(() => {
    if (!runParam || expanded || all.length === 0) return;
    const match = all.find(
      (e) =>
        e.id === runParam ||
        e.id.startsWith(runParam) ||
        runParam.startsWith(e.id) ||
        // Agents stash thread_id under log.input.thread_id.
        (() => {
          const input =
            (e.log as { input?: { thread_id?: string } })?.input ?? null;
          return input?.thread_id === runParam;
        })(),
    );
    if (match) setExpanded(match);
  }, [runParam, expanded, all]);

  // Filter pipeline: kind → color → outcome → agent role → date range → search.
  const searchTerm = search.trim().toLowerCase();
  const dateCutoff =
    dateRange === "all" ? null : Date.now() - DATE_RANGE_MS[dateRange];
  const visible = all
    .filter((e) => filter === "all" || e.kind === filter)
    .filter((e) => colorFilter === "all" || e.color === colorFilter)
    .filter((e) => outcomeFilter === "all" || e.outcome === outcomeFilter)
    .filter((e) => agentFilter === "all" || (
      e.kind === "agent" && String(e.log.agent ?? "unknown") === agentFilter
    ))
    .filter((e) => {
      if (dateCutoff === null) return true;
      const ts = e.started_at ? new Date(e.started_at).getTime() : 0;
      return ts >= dateCutoff;
    })
    .filter((e) => {
      if (!searchTerm) return true;
      const hay = [
        e.title,
        e.subtitle ?? "",
        e.synopsis ?? "",
        e.run_slug,
        e.raw_status,
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(searchTerm);
    });

  const counts: Record<StatusColor, number> = {
    active: all.filter((e) => e.color === "active").length,
    pending: all.filter((e) => e.color === "pending").length,
    completed: all.filter((e) => e.color === "completed").length,
    failed: all.filter((e) => e.color === "failed").length,
  };

  // Bulk-cancel targets: in-flight tasks + running agents. Deferred work needs
  // an explicit per-card disposition so it is never swept away accidentally.
  const activeForBulkCancel = all.filter(
    (e) =>
      (e.kind === "task" &&
        ["pending", "dispatched", "running"].includes(e.raw_status)) ||
      (e.kind === "agent" && e.raw_status === "running"),
  );

  const onBulkCancel = useCallback(async () => {
    if (activeForBulkCancel.length === 0) return;
    setBulkCancelling(true);
    setLocalError(null);
    const failures: string[] = [];
    await Promise.allSettled(
      activeForBulkCancel.map((entity) =>
        entity.kind === "task"
          ? cancelTaskMutation.mutateAsync(entity.id)
          : cancelAgentMutation.mutateAsync(entity.id),
      ),
    ).then((results) => {
      results.forEach((r, i) => {
        if (r.status === "rejected") {
          failures.push(
            `${activeForBulkCancel[i].title}: ${
              r.reason instanceof Error ? r.reason.message : String(r.reason)
            }`,
          );
        }
      });
    });
    if (failures.length) {
      setLocalError(`Some runs could not be cancelled: ${failures.join("; ")}`);
    }
    setBulkCancelling(false);
  }, [activeForBulkCancel, cancelTaskMutation, cancelAgentMutation]);

  return (
    <div className="space-y-6">
      {/* Top metrics row — also doubles as click-to-filter chips */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(["active", "pending", "completed", "failed"] as const).map((c) => (
          <button
            key={c}
            type="button"
            onClick={() =>
              setColorFilter((prev) => (prev === c ? "all" : c))
            }
            className={cn(
              "rounded-lg border p-4 text-left transition-colors",
              COLOR_BORDER[c],
              colorFilter === c ? "ring-1 ring-foreground/40" : "",
            )}
          >
            <div className="text-2xl font-semibold tabular-nums">
              {counts[c]}
            </div>
            <div className="mt-1 flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted-foreground">
              {COLOR_LABEL[c]}
            </div>
          </button>
        ))}
      </div>

      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="flex flex-wrap items-center gap-1">
          {KIND_FILTERS.map((opt) => (
            <button
              key={opt}
              type="button"
              onClick={() => setFilter(opt)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs transition-colors",
                filter === opt
                  ? "border-critical/50 bg-critical/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {opt === "all" ? "All kinds" : `${KIND_LABEL[opt]}s`}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Outcome:
          </span>
          {(["all", "success", "empty", "partial", "errored"] as const).map((opt) => (
            <button
              key={opt}
              type="button"
              onClick={() => setOutcomeFilter(opt)}
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
                outcomeFilter === opt
                  ? "border-foreground bg-foreground text-background"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {opt === "all" ? "All" : OUTCOME_LABEL[opt]}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
          Agent:
          <select
            value={agentFilter}
            onChange={(event) => setAgentFilter(event.target.value)}
            className="h-7 rounded-full border border-border bg-background px-2 text-[11px] normal-case text-foreground"
          >
            <option value="all">All roles</option>
            {agentRoles.map((role) => (
              <option key={role} value={role}>{role}</option>
            ))}
          </select>
        </label>
        {/* v1.2.0 date-range chips */}
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Since:
          </span>
          {DATE_RANGE_FILTERS.map((opt) => (
            <button
              key={opt}
              type="button"
              onClick={() => setDateRange(opt)}
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
                dateRange === opt
                  ? "border-foreground bg-foreground text-background"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {opt === "all" ? "All time" : opt}
            </button>
          ))}
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void onBulkCancel()}
          disabled={bulkCancelling || activeForBulkCancel.length === 0}
          className="ml-2"
        >
          <CircleSlash className="mr-1.5 h-3.5 w-3.5" />
          {bulkCancelling
            ? "Cancelling…"
            : `Cancel all active (${activeForBulkCancel.length})`}
        </Button>
        <div className="ml-auto flex rounded-md border border-border p-0.5">
          <button
            type="button"
            onClick={() => setViewMode("cards")}
            className={cn("rounded p-1.5", viewMode === "cards" ? "bg-muted text-foreground" : "text-muted-foreground")}
            aria-label="Card view"
            title="Card view"
          >
            <LayoutGrid className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setViewMode("table")}
            className={cn("rounded p-1.5", viewMode === "table" ? "bg-muted text-foreground" : "text-muted-foreground")}
            aria-label="Table view"
            title="Table view"
          >
            <List className="h-3.5 w-3.5" />
          </button>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void refetch()}
        >
          <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>

      {/* v1.2.0 typed search over title / subtitle / synopsis / run_slug */}
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search runs — title, tool, target, rt-slug…"
          className="w-full rounded-md border border-border bg-background py-2 pl-8 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus:border-foreground focus:outline-none"
        />
      </div>

      {error && <p className="text-sm text-critical">{error}</p>}

      {/* Box grid */}
      {data == null && !error ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : visible.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing to show. {filter !== "all" || colorFilter !== "all" || outcomeFilter !== "all" || agentFilter !== "all"
            ? "Try clearing filters."
            : "Run an agent or kick off a task to populate the timeline."}
        </p>
      ) : (
        // v2.5.1: cap the primary run list to the first 8 rows so the
        // Attribution panel below is visible on Status entry. Analyst
        // can click Show all to see the full filtered list inside a
        // bounded scroll container.
        (() => {
          const INITIAL_RUN_CAP = 8;
          const shown = showAllRuns ? visible : visible.slice(0, INITIAL_RUN_CAP);
          const hidden = Math.max(0, visible.length - shown.length);
          const listWrapperClass = showAllRuns
            ? "max-h-[36rem] overflow-y-auto pr-1"
            : "";
          return (
            <div className="space-y-2">
              {viewMode === "cards" ? (
                <div className={cn(listWrapperClass)}>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    {shown.map((entity) => (
                      <StatusBox
                        key={`${entity.kind}-${entity.id}`}
                        entity={entity}
                        onExpand={() => setExpanded(entity)}
                        onRetry={() => onRetry(entity)}
                        onCancel={() => onCancel(entity)}
                        retrying={retryingId === entity.id}
                        cancelling={cancellingId === entity.id}
                      />
                    ))}
                  </div>
                </div>
              ) : (
                <div
                  className={cn(
                    "overflow-x-auto rounded-lg border border-border bg-card/40",
                    showAllRuns && "max-h-[36rem] overflow-y-auto",
                  )}
                >
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border text-left text-[10px] uppercase tracking-wide text-muted-foreground">
                        <th className="px-3 py-2">Run</th>
                        <th className="px-3 py-2">Kind / role</th>
                        <th className="px-3 py-2">Status</th>
                        <th className="px-3 py-2">Outcome</th>
                        <th className="px-3 py-2">Started</th>
                        <th className="px-3 py-2">Synopsis</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((entity) => (
                        <tr
                          key={`${entity.kind}-${entity.id}`}
                          onClick={() => setExpanded(entity)}
                          className="cursor-pointer border-b border-border/50 last:border-0 hover:bg-muted/30"
                        >
                          <td className="px-3 py-2">
                            <p className="font-medium">{entity.title}</p>
                            <p className="font-mono text-[10px] text-muted-foreground">{entity.run_slug}</p>
                          </td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            {entity.kind === "agent" ? String(entity.log.agent ?? "agent") : entity.kind}
                          </td>
                          <td className="px-3 py-2">
                            <Badge variant="outline" className={COLOR_BADGE[entity.color]}>
                              {COLOR_LABEL[entity.color]}
                            </Badge>
                          </td>
                          <td className="px-3 py-2">
                            {entity.outcome ? (
                              <Badge variant="outline" className={OUTCOME_CLASS[entity.outcome]}>
                                {OUTCOME_LABEL[entity.outcome]}
                              </Badge>
                            ) : "—"}
                          </td>
                          <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground"><DateTime value={entity.started_at} /></td>
                          <td className="max-w-md px-3 py-2 text-xs text-muted-foreground">{entity.synopsis ?? entity.subtitle ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {(hidden > 0 || showAllRuns) && (
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>
                    {showAllRuns
                      ? `Showing all ${visible.length} runs (scrollable)`
                      : `Showing first ${shown.length} of ${visible.length} runs`}
                  </span>
                  <button
                    type="button"
                    onClick={() => setShowAllRuns((prev) => !prev)}
                    className="rounded border border-border px-2 py-1 text-xs hover:bg-secondary/60"
                  >
                    {showAllRuns
                      ? `Show first ${INITIAL_RUN_CAP}`
                      : `Show all ${visible.length}`}
                  </button>
                </div>
              )}
            </div>
          );
        })()
      )}

      {/* v2.4.0: attribution — who used what (agent × model × user)
          for this engagement. Lives below the task/approval tables so
          the primary Status content isn't pushed down. */}
      <AttributionTable slug={slug} />

      {/* Detail popup */}
      {expanded && (
        <ExpandedDetail
          slug={slug}
          entity={expanded}
          onClose={() => setExpanded(null)}
        />
      )}
    </div>
  );
}

// v0.8.2: status transition timeline rendered at the top of the Expand
// modal. Each entry is a colour-coded dot + label + timestamp. Active
// rows that haven't reached terminal show a pulsing dot so the analyst
// sees the box is still in flight.
function StatusTimeline({ history }: { history: StatusTransition[] }) {
  if (history.length === 0) return null;
  return (
    <div className="mt-3 rounded-md border border-border bg-secondary/30 p-3">
      <p className="mb-2 text-[10px] uppercase tracking-wide text-muted-foreground">
        Status timeline
      </p>
      <ol className="space-y-1.5">
        {history.map((t, idx) => {
          const isLast = idx === history.length - 1;
          const isActive = t.status === "active" && isLast;
          return (
            <li key={`${t.status}-${t.at}`} className="flex items-center gap-3">
              <span
                className={cn(
                  "h-2.5 w-2.5 rounded-full border",
                  STATUS_DOT_CLASS[t.status],
                  isActive && "animate-pulse",
                )}
              />
              <span className="text-xs font-medium uppercase tracking-wide text-foreground">
                {COLOR_LABEL[t.status]}
              </span>
              <span className="text-[10px] text-muted-foreground">
                {t.raw_status}
              </span>
              <span className="ml-auto text-[10px] text-muted-foreground">
                {new Date(t.at).toLocaleString(undefined, {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

const STATUS_DOT_CLASS: Record<StatusColor, string> = {
  active: "border-emerald-400 bg-emerald-500/80",
  pending: "border-sky-400 bg-sky-500/80",
  completed: "border-violet-400 bg-violet-500/80",
  failed: "border-rose-400 bg-rose-500/80",
};

function StatusBox({
  entity,
  onExpand,
  onRetry,
  onCancel,
  retrying,
  cancelling,
}: {
  entity: StatusEntity;
  onExpand: () => void;
  onRetry: () => void;
  onCancel: () => void;
  retrying: boolean;
  cancelling: boolean;
}) {
  const Icon = COLOR_ICON[entity.color];
  const OutcomeIcon = entity.outcome ? OUTCOME_ICON[entity.outcome] : null;
  const cancellable =
    (entity.kind === "task" &&
      ["pending", "deferred", "dispatched", "running"].includes(entity.raw_status)) ||
    (entity.kind === "agent" && entity.raw_status === "running");
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-lg border bg-card/40 p-3 transition-colors",
        COLOR_BORDER[entity.color],
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium leading-snug text-foreground">
            {entity.title}
          </p>
          {entity.subtitle && (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {entity.subtitle}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span
            className={cn(
              "rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide",
              COLOR_BADGE[entity.color],
            )}
          >
            <Icon className="-mt-0.5 mr-1 inline-block h-3 w-3" />
            {COLOR_LABEL[entity.color]}
          </span>
          {/* v1.2.0: outcome sub-badge under the colour pill */}
          {entity.outcome && OutcomeIcon && (
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide",
                OUTCOME_CLASS[entity.outcome],
              )}
            >
              <OutcomeIcon className="-mt-0.5 mr-1 inline-block h-3 w-3" />
              {OUTCOME_LABEL[entity.outcome]}
            </span>
          )}
        </div>
      </div>
      {/* v1.2.0: plain-language synopsis */}
      {entity.synopsis && (
        <p className="text-xs italic text-muted-foreground">
          {entity.synopsis}
        </p>
      )}
      <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
        <span className="font-mono">{entity.run_slug}</span>
        <span>
          {KIND_LABEL[entity.kind]} · {entity.raw_status}
        </span>
        <span>
          {entity.started_at && (
            <>started <DateTime value={entity.started_at} /></>
          )}
          {entity.completed_at && (
            <> · ended <DateTime value={entity.completed_at} /></>
          )}
        </span>
      </div>
      <div className="flex justify-end gap-2">
        {cancellable && (
          <Button
            size="sm"
            variant="outline"
            onClick={onCancel}
            disabled={cancelling}
          >
            <CircleSlash className="mr-1.5 h-3.5 w-3.5" />
            {cancelling ? "Cancelling…" : "Cancel"}
          </Button>
        )}
        {entity.retryable && (
          <Button
            size="sm"
            variant="outline"
            onClick={onRetry}
            disabled={retrying}
          >
            <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
            {retrying
              ? "Dispatching…"
              : entity.raw_status === "deferred"
                ? "Run now"
                : "Retry"}
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onExpand}>
          Expand
        </Button>
      </div>
    </div>
  );
}

function ExpandedDetail({
  slug,
  entity,
  onClose,
}: {
  slug: string;
  entity: StatusEntity;
  onClose: () => void;
}) {
  // v1.2.0: fetch the per-entity step log lazily (query only fires
  // because ExpandedDetail is mounted). If the entity has reached a
  // terminal colour, disable the 3s polling — nothing new will arrive.
  const isTerminal = entity.color === "completed" || entity.color === "failed";
  const stepsQuery = useStatusSteps(slug, entity.kind, entity.id, {
    liveTerminal: isTerminal,
  });
  return (
    <>
      <div
        className="fixed inset-0 z-[60] bg-black/70"
        onClick={onClose}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Status detail"
        className="fixed left-1/2 top-1/2 z-[70] flex max-h-[85vh] w-[min(800px,94vw)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-border bg-popover p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-sm font-semibold text-foreground">
              {entity.title}
              <span className="ml-2 font-mono text-[11px] text-muted-foreground">
                {entity.run_slug}
              </span>
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {KIND_LABEL[entity.kind]} · {entity.raw_status}
              {entity.started_at && <> · started <DateTime value={entity.started_at} /></>}
              {entity.completed_at && (
                <> · ended <DateTime value={entity.completed_at} /></>
              )}
            </p>
            {entity.synopsis && (
              <p className="mt-1 text-xs italic text-muted-foreground">
                {entity.synopsis}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        {/* v0.8.2: timeline at the top so the analyst sees the
            transition trail BEFORE the raw payload dump. */}
        <StatusTimeline history={entity.history} />
        {/* v1.2.0: step log lands between the timeline and the JSON
            payload — the analyst-readable trace of what actually
            happened, beat by beat. */}
        <StepLog query={stepsQuery} />
        <details className="mt-3 flex-1 overflow-hidden rounded-md border border-border bg-background">
          <summary className="cursor-pointer select-none px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">
            Raw payload (JSON)
          </summary>
          <pre className="max-h-64 overflow-auto p-3 font-mono text-xs text-muted-foreground">
            {JSON.stringify(entity.log, null, 2)}
          </pre>
        </details>
      </div>
    </>
  );
}

// v1.2.0: renders the fetched step log. Each row is ``ts · kind ·
// label`` with ``detail`` toggleable in a monospace block.
function StepLog({
  query,
}: {
  query: ReturnType<typeof useStatusSteps>;
}) {
  const [openIdx, setOpenIdx] = useState<Set<number>>(new Set());
  const toggle = useCallback((i: number) => {
    setOpenIdx((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }, []);
  if (query.isLoading) {
    return (
      <div className="mt-3 rounded-md border border-border bg-secondary/30 p-3">
        <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Step log
        </p>
        <p className="mt-2 text-xs text-muted-foreground">Loading…</p>
      </div>
    );
  }
  if (query.error) {
    return (
      <div className="mt-3 rounded-md border border-border bg-secondary/30 p-3">
        <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Step log
        </p>
        <p className="mt-2 text-xs text-critical">
          Failed to load steps:{" "}
          {query.error instanceof Error
            ? query.error.message
            : String(query.error)}
        </p>
      </div>
    );
  }
  const data = query.data;
  const steps = data?.steps ?? [];
  return (
    <div className="mt-3 rounded-md border border-border bg-secondary/30 p-3">
      <div className="flex items-center justify-between">
        <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Step log ({steps.length}
          {data?.truncated ? "+ truncated" : ""})
        </p>
      </div>
      {steps.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">
          No steps recorded for this entity yet.
        </p>
      ) : (
        <ol className="mt-2 space-y-1">
          {steps.map((s, i) => {
            const isOpen = openIdx.has(i);
            const hasDetail =
              s.detail && Object.keys(s.detail).length > 0;
            return (
              <li
                key={`${s.kind}-${s.at}-${i}`}
                className="rounded border-l-2 border-border bg-background/60 px-2 py-1.5"
              >
                <button
                  type="button"
                  onClick={() => hasDetail && toggle(i)}
                  className={cn(
                    "flex w-full items-start gap-2 text-left",
                    hasDetail && "cursor-pointer",
                  )}
                  aria-expanded={isOpen}
                >
                  <span className="mt-0.5 shrink-0 font-mono text-[10px] text-muted-foreground">
                    {new Date(s.at).toLocaleTimeString(undefined, {
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                    })}
                  </span>
                  <Badge
                    variant="outline"
                    className="shrink-0 text-[10px]"
                  >
                    {s.kind}
                  </Badge>
                  <span className="min-w-0 flex-1 text-xs text-foreground">
                    {s.label}
                  </span>
                  {hasDetail &&
                    (isOpen ? (
                      <ChevronDown className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    ))}
                </button>
                {isOpen && hasDetail && (
                  <pre className="mt-1.5 overflow-auto rounded bg-background p-2 font-mono text-[11px] text-muted-foreground">
                    {JSON.stringify(s.detail, null, 2)}
                  </pre>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
