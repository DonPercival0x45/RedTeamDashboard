"use client";

// v1.2.0: /settings/agent-runs — tenant-global runs.
//
// Planner rank/combine/re-evaluate + admin roadmap ops produce
// AgentExecution rows with engagement_id == NULL. This page is where
// the toast's "Open →" lands for those. Also serves as an audit view
// of "what did the planner do lately" for admins.
//
// Uses the same StatusBox / ExpandedDetail shape as the engagement
// Status tab — but with a slimmer feed (agents only) and a
// tenant-global steps hook.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleSlash,
  Clock,
  RefreshCcw,
  Search,
  Slash,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useCancelAgentExecutionMutation,
  useGlobalAgentRunSteps,
  useGlobalAgentRuns,
} from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type {
  StatusColor,
  StatusEntity,
  StatusOutcome,
} from "@/lib/types";

const COLOR_BADGE: Record<StatusColor, string> = {
  active: "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
  pending: "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-200",
  completed: "border-violet-500/50 bg-violet-500/10 text-violet-700 dark:text-violet-200",
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

function fmtDate(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function AgentRunsPage() {
  const params = useSearchParams();
  const { data, error, refetch } = useGlobalAgentRuns();
  const cancelAgentRun = useCancelAgentExecutionMutation();
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<StatusEntity | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const agents = data?.agents ?? [];

  // v1.2.0: deep-link. If `?run=<id>` in the URL, auto-open Expand on
  // the matching row once it appears in the feed.
  const runParam = params?.get("run") ?? null;
  useEffect(() => {
    if (!runParam || expanded) return;
    const match = agents.find(
      (a) => a.id === runParam || a.id.startsWith(runParam),
    );
    if (match) setExpanded(match);
  }, [runParam, expanded, agents]);

  const term = search.trim().toLowerCase();
  const visible = agents.filter((e) => {
    if (!term) return true;
    const hay = [e.title, e.subtitle ?? "", e.synopsis ?? "", e.run_slug]
      .join(" ")
      .toLowerCase();
    return hay.includes(term);
  });

  const onCancel = useCallback(
    async (entity: StatusEntity) => {
      setCancellingId(entity.id);
      setLocalError(null);
      try {
        await cancelAgentRun.mutateAsync(entity.id);
      } catch (err) {
        setLocalError(err instanceof Error ? err.message : String(err));
      } finally {
        setCancellingId(null);
      }
    },
    [cancelAgentRun],
  );

  const errMsg = localError ?? (error instanceof Error ? error.message : error ? String(error) : null);

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6">
      <div>
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← engagements
        </Link>
        <div className="mt-2 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              Agent runs
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Tenant-global runs — planner rank / combine / re-evaluate,
              admin roadmap ops. Engagement-scoped runs live on each
              engagement&apos;s Status tab.
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={() => void refetch()}>
            <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
            Refresh
          </Button>
        </div>
      </div>

      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search runs — title, model, rt-slug…"
          className="w-full rounded-md border border-border bg-background py-2 pl-8 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus:border-foreground focus:outline-none"
        />
      </div>

      {errMsg && <p className="text-sm text-critical">{errMsg}</p>}

      {data == null && !errMsg ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : visible.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No tenant-global agent runs yet. Kick off a Prioritize / Combine
          op on <Link href="/settings/feedback" className="underline">Feedback</Link>{" "}
          to populate.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {visible.map((entity) => (
            <StatusBox
              key={entity.id}
              entity={entity}
              onExpand={() => setExpanded(entity)}
              onCancel={() => onCancel(entity)}
              cancelling={cancellingId === entity.id}
            />
          ))}
        </div>
      )}

      {expanded && (
        <ExpandedDetail
          entity={expanded}
          onClose={() => setExpanded(null)}
        />
      )}
    </div>
  );
}

function StatusBox({
  entity,
  onExpand,
  onCancel,
  cancelling,
}: {
  entity: StatusEntity;
  onExpand: () => void;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const Icon = COLOR_ICON[entity.color];
  const OutcomeIcon = entity.outcome ? OUTCOME_ICON[entity.outcome] : null;
  const cancellable = entity.raw_status === "running";
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
      {entity.synopsis && (
        <p className="text-xs italic text-muted-foreground">
          {entity.synopsis}
        </p>
      )}
      <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
        <span className="font-mono">{entity.run_slug}</span>
        <span>
          {entity.started_at && <>started {fmtDate(entity.started_at)}</>}
          {entity.completed_at && (
            <> · ended {fmtDate(entity.completed_at)}</>
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
        <Button size="sm" variant="ghost" onClick={onExpand}>
          Expand
        </Button>
      </div>
    </div>
  );
}

function ExpandedDetail({
  entity,
  onClose,
}: {
  entity: StatusEntity;
  onClose: () => void;
}) {
  const isTerminal = entity.color === "completed" || entity.color === "failed";
  const stepsQuery = useGlobalAgentRunSteps(entity.id, {
    liveTerminal: isTerminal,
  });
  const [openIdx, setOpenIdx] = useState<Set<number>>(new Set());
  const toggle = useCallback((i: number) => {
    setOpenIdx((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }, []);
  const steps = stepsQuery.data?.steps ?? [];
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
        aria-label="Agent run detail"
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
              {entity.raw_status}
              {entity.started_at && <> · started {fmtDate(entity.started_at)}</>}
              {entity.completed_at && (
                <> · ended {fmtDate(entity.completed_at)}</>
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
        <div className="mt-3 rounded-md border border-border bg-secondary/30 p-3">
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Step log ({steps.length}
            {stepsQuery.data?.truncated ? "+ truncated" : ""})
          </p>
          {stepsQuery.isLoading ? (
            <p className="mt-2 text-xs text-muted-foreground">Loading…</p>
          ) : steps.length === 0 ? (
            <p className="mt-2 text-xs text-muted-foreground">
              No steps recorded for this run.
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
                      className="flex w-full items-start gap-2 text-left"
                      aria-expanded={isOpen}
                    >
                      <span className="mt-0.5 shrink-0 font-mono text-[10px] text-muted-foreground">
                        {new Date(s.at).toLocaleTimeString(undefined, {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                      </span>
                      <Badge variant="outline" className="shrink-0 text-[10px]">
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
