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

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  RefreshCcw,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useEngagementStatus,
  useRetryAgentExecutionMutation,
  useRetryTaskMutation,
} from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type {
  LoggedEvent,
  RunEvent,
  StatusColor,
  StatusEntity,
  StatusKind,
  StatusTransition,
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

// v0.8.2: Live events log helpers — used to lifted from event-log.tsx.

const EVENT_COLORS: Record<RunEvent["type"], string> = {
  "run.started": "border-sky-500 text-sky-200",
  "approval.pending": "border-amber-500 text-amber-200",
  "tool.denied": "border-orange-500 text-orange-200",
  "tool.auto_approved": "border-violet-500 text-violet-200",
  "finding.created": "border-emerald-500 text-emerald-200",
  "run.completed": "border-zinc-500 text-zinc-300",
  "run.errored": "border-rose-500 text-rose-200",
};

function summarizeEvent(event: RunEvent): string {
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
  events = [],
}: {
  slug: string;
  events?: LoggedEvent[];
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

  const [localError, setLocalError] = useState<string | null>(null);
  const error = localError ?? (queryError instanceof Error ? queryError.message : queryError ? String(queryError) : null);

  const [filter, setFilter] = useState<StatusKind | "all">("all");
  const [colorFilter, setColorFilter] = useState<StatusColor | "all">("all");
  const [expanded, setExpanded] = useState<StatusEntity | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  // v0.8.2: Live events panel (folded in from the standalone Event log).
  // Default collapsed so the box grid stays the focus.
  const [liveEventsOpen, setLiveEventsOpen] = useState(false);
  const eventsScrollRef = useRef<HTMLUListElement | null>(null);

  // Auto-scroll the events panel to the bottom whenever a new event lands
  // and the panel is open. Doing this in an effect keeps the scroll
  // logic out of the render path.
  useEffect(() => {
    if (!liveEventsOpen) return;
    const el = eventsScrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length, liveEventsOpen]);

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
          onClick={() => void refetch()}
          className="ml-auto"
        >
          <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>

      {error && <p className="text-sm text-critical">{error}</p>}

      {/* Box grid */}
      {data == null && !error ? (
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

      {/* v0.8.2: Live events panel (replaces the standalone Event log
          card at the bottom of the engagement page). Collapsed by
          default; expand to see the SSE tail. */}
      <div className="rounded-lg border border-border bg-card/40">
        <button
          type="button"
          onClick={() => setLiveEventsOpen((v) => !v)}
          className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left"
          aria-expanded={liveEventsOpen}
        >
          <div className="flex items-center gap-2">
            {liveEventsOpen ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            )}
            <span className="text-sm font-medium">Live events</span>
            <span className="text-xs text-muted-foreground">
              ({events.length})
            </span>
          </div>
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            SSE tail · runs:&lt;eid&gt;:events
          </span>
        </button>
        {liveEventsOpen && (
          <div className="border-t border-border p-3">
            {events.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Waiting for events. Start a run to populate.
              </p>
            ) : (
              <ul
                ref={eventsScrollRef}
                className="max-h-72 space-y-1.5 overflow-y-auto font-mono text-xs"
              >
                {events
                  .slice()
                  .reverse()
                  .map((entry) => (
                    <li
                      key={entry.sseId}
                      className="flex items-start gap-2 rounded border-l-2 border-border bg-secondary/30 px-2 py-1.5"
                    >
                      <Badge
                        variant="outline"
                        className={cn(
                          "shrink-0 text-[10px]",
                          EVENT_COLORS[entry.event.type] ?? "",
                        )}
                      >
                        {entry.event.type}
                      </Badge>
                      <span className="break-all text-muted-foreground">
                        {summarizeEvent(entry.event)}
                      </span>
                    </li>
                  ))}
              </ul>
            )}
          </div>
        )}
      </div>

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
        {/* v0.8.2: timeline at the top so the analyst sees the
            transition trail BEFORE the raw payload dump. */}
        <StatusTimeline history={entity.history} />
        <pre className="mt-4 flex-1 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-xs text-muted-foreground">
          {JSON.stringify(entity.log, null, 2)}
        </pre>
      </div>
    </>
  );
}
