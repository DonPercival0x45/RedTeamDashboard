"use client";

// Status tab (v0.8.0). Per-engagement timeline of LLM agent calls,
// orchestrator tasks, and approval gates. Color-coded per the brief:
//
//   green   active     — currently running / in flight
//   blue    pending    — queued, awaiting action
//   purple  completed  — terminal success
//   red     failed     — terminal failure (retry button shows here)
//
// Each box has an Expand control that pops a modal with the raw input /
// output JSONB the backend returned. Failed tasks expose a Retry button;
// failed agents are display-only for now (per-kind dispatch coming next
// commit) and approvals are decided via the existing approval flow, not
// retried.

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  CheckCircle2,
  Clock,
  RefreshCcw,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { getEngagementStatus, retryTask } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  EngagementStatusResponse,
  StatusColor,
  StatusEntity,
  StatusKind,
} from "@/lib/types";

const COLOR_BADGE: Record<StatusColor, string> = {
  active:
    "border-emerald-500/50 bg-emerald-500/10 text-emerald-200",
  pending: "border-sky-500/50 bg-sky-500/10 text-sky-200",
  completed:
    "border-violet-500/50 bg-violet-500/10 text-violet-200",
  failed: "border-rose-500/50 bg-rose-500/10 text-rose-200",
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

export function StatusView({ slug }: { slug: string }) {
  const [data, setData] = useState<EngagementStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<StatusKind | "all">("all");
  const [colorFilter, setColorFilter] = useState<StatusColor | "all">("all");
  const [expanded, setExpanded] = useState<StatusEntity | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const next = await getEngagementStatus(slug);
      setData(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [slug]);

  useEffect(() => {
    void reload();
    // Light auto-refresh so active rows tick toward terminal without a
    // manual reload. 8s feels good for an analyst-visible feed.
    const t = setInterval(() => {
      void reload();
    }, 8000);
    return () => clearInterval(t);
  }, [reload]);

  const onRetry = useCallback(
    async (entity: StatusEntity) => {
      if (entity.kind !== "task") return;
      setRetryingId(entity.id);
      try {
        await retryTask(entity.id);
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setRetryingId(null);
      }
    },
    [reload],
  );

  const all: StatusEntity[] = data
    ? [...data.agents, ...data.tasks, ...data.approvals].sort((a, b) => {
        const ta = (a.started_at || a.completed_at || "").toString();
        const tb = (b.started_at || b.completed_at || "").toString();
        return tb.localeCompare(ta);
      })
    : [];

  const visible = all
    .filter((e) => filter === "all" || e.kind === filter)
    .filter((e) => colorFilter === "all" || e.color === colorFilter);

  const counts: Record<StatusColor, number> = {
    active: all.filter((e) => e.color === "active").length,
    pending: all.filter((e) => e.color === "pending").length,
    completed: all.filter((e) => e.color === "completed").length,
    failed: all.filter((e) => e.color === "failed").length,
  };

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
        <Button
          size="sm"
          variant="outline"
          onClick={() => void reload()}
          className="ml-auto"
        >
          <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>

      {error && <p className="text-sm text-critical">{error}</p>}

      {/* Box grid */}
      {data === null && !error ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : visible.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing to show. {filter !== "all" || colorFilter !== "all"
            ? "Try clearing filters."
            : "Run an agent or kick off a task to populate the timeline."}
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {visible.map((entity) => (
            <StatusBox
              key={`${entity.kind}-${entity.id}`}
              entity={entity}
              onExpand={() => setExpanded(entity)}
              onRetry={() => onRetry(entity)}
              retrying={retryingId === entity.id}
            />
          ))}
        </div>
      )}

      {/* Detail popup */}
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
  onRetry,
  retrying,
}: {
  entity: StatusEntity;
  onExpand: () => void;
  onRetry: () => void;
  retrying: boolean;
}) {
  const Icon = COLOR_ICON[entity.color];
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
        <span
          className={cn(
            "shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide",
            COLOR_BADGE[entity.color],
          )}
        >
          <Icon className="-mt-0.5 mr-1 inline-block h-3 w-3" />
          {COLOR_LABEL[entity.color]}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
        <span>
          {KIND_LABEL[entity.kind]} · {entity.raw_status}
        </span>
        <span>
          {entity.started_at && (
            <>started {fmtDate(entity.started_at)}</>
          )}
          {entity.completed_at && (
            <> · ended {fmtDate(entity.completed_at)}</>
          )}
        </span>
      </div>
      <div className="flex justify-end gap-2">
        {entity.retryable && (
          <Button
            size="sm"
            variant="outline"
            onClick={onRetry}
            disabled={retrying}
          >
            <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
            {retrying ? "Retrying…" : "Retry"}
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
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {KIND_LABEL[entity.kind]} · {entity.raw_status}
              {entity.started_at && <> · started {fmtDate(entity.started_at)}</>}
              {entity.completed_at && (
                <> · ended {fmtDate(entity.completed_at)}</>
              )}
            </p>
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
        <pre className="mt-4 flex-1 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-xs text-muted-foreground">
          {JSON.stringify(entity.log, null, 2)}
        </pre>
      </div>
    </>
  );
}
