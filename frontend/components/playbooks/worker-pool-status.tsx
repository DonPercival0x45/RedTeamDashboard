"use client";

import { AlertTriangle, Circle, LoaderCircle, ServerCog } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { WorkerPoolStatus, WorkerSlotStatus } from "@/lib/types";

const STATE_STYLE: Record<WorkerSlotStatus["state"], string> = {
  idle: "border-emerald-500/40 bg-emerald-500/5",
  busy: "border-sky-500/50 bg-sky-500/10",
  starting: "border-amber-500/40 bg-amber-500/5",
  untracked: "border-amber-500/50 bg-amber-500/10",
  failed: "border-rose-500/50 bg-rose-500/10",
  offline: "border-zinc-500/40 bg-zinc-500/5",
};

function ageLabel(seconds: number | null): string {
  if (seconds === null) return "unknown";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
}

function SlotIcon({ state, stale }: { state: WorkerSlotStatus["state"]; stale: boolean }) {
  if (stale) {
    return <AlertTriangle aria-hidden="true" className="h-4 w-4 text-zinc-500" />;
  }
  if (state === "busy" || state === "untracked") {
    return (
      <LoaderCircle
        aria-hidden="true"
        className={cn(
          "h-4 w-4 motion-safe:animate-spin",
          state === "untracked" ? "text-amber-600" : "text-sky-600",
        )}
      />
    );
  }
  if (state === "failed" || state === "offline") {
    return <AlertTriangle aria-hidden="true" className="h-4 w-4 text-rose-600" />;
  }
  return (
    <Circle
      aria-hidden="true"
      className={cn(
        "h-3.5 w-3.5 fill-current",
        state === "idle" ? "text-emerald-500" : "text-amber-500",
      )}
    />
  );
}

export function WorkerPoolPanel({
  pool,
  engagementSlug,
  onOpenRun,
  stale = false,
}: {
  pool: WorkerPoolStatus;
  engagementSlug: string;
  onOpenRun: (runId: string) => void;
  stale?: boolean;
}) {
  const summary = stale
    ? "Worker telemetry refresh failed; displayed progress is cached"
    : pool.health === "unavailable"
      ? "No fresh playbook-worker heartbeat"
      : `${pool.busy} working · ${pool.idle} ready · ${pool.pending_depth} queued here`;
  const displayedHealth = stale ? "unknown" : pool.health;

  return (
    <section
      aria-labelledby="worker-pool-heading"
      className="space-y-3 rounded-lg border border-border bg-card p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="worker-pool-heading" className="flex items-center gap-2 text-sm font-semibold">
            <ServerCog aria-hidden="true" className="h-4 w-4" />
            Playbook workers
          </h2>
          <p
            className="mt-1 text-xs text-muted-foreground"
            role={stale ? "alert" : "status"}
            aria-live={stale ? "assertive" : "polite"}
          >
            {summary}
          </p>
          {!stale && pool.oldest_pending_age_seconds !== null ? (
            <p className="mt-0.5 text-[10px] text-muted-foreground">
              Oldest wait {ageLabel(pool.oldest_pending_age_seconds)}
            </p>
          ) : null}
        </div>
        <span
          className={cn(
            "rounded-full border px-2 py-1 text-[10px] font-medium uppercase tracking-wide",
            displayedHealth === "healthy"
              ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-300"
              : displayedHealth === "degraded"
                ? "border-amber-500/40 text-amber-700 dark:text-amber-300"
                : "border-rose-500/40 text-rose-700 dark:text-rose-300",
          )}
        >
          {displayedHealth}
        </span>
      </div>

      {pool.slots.length === 0 ? (
        <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
          Worker telemetry has not registered a playbook lane yet.
        </p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2" aria-label="Worker slots">
          {pool.slots.map((slot) => {
            const run = slot.current_run;
            const percent =
              run && run.steps_total > 0
                ? Math.min(100, Math.round((run.steps_completed / run.steps_total) * 100))
                : 0;
            return (
              <article
                key={slot.id}
                className={cn(
                  "rounded-md border p-3",
                  stale ? "border-zinc-500/40 bg-zinc-500/5" : STATE_STYLE[slot.state],
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 text-xs font-medium">
                    <SlotIcon state={slot.state} stale={stale} />
                    Worker {slot.slot + 1}
                  </div>
                  <span className="text-[10px] uppercase text-muted-foreground">
                    {stale ? "stale" : slot.state}
                  </span>
                </div>

                {run ? (
                  <div className="mt-2 space-y-2">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-xs">
                        {run.playbook_name}
                        {run.engagement_slug !== engagementSlug ? (
                          <span className="text-muted-foreground"> · {run.engagement_slug}</span>
                        ) : null}
                      </p>
                      {run.engagement_slug === engagementSlug ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 px-2 text-[10px]"
                          onClick={() => onOpenRun(run.id)}
                        >
                          Open
                        </Button>
                      ) : null}
                    </div>
                    <div
                      className="h-1.5 overflow-hidden rounded-full bg-background/70"
                      role="progressbar"
                      aria-label={`${run.playbook_name} progress`}
                      aria-valuemin={0}
                      aria-valuemax={run.steps_total}
                      aria-valuenow={run.steps_completed}
                    >
                      <div
                        className="h-full rounded-full bg-sky-500 transition-[width]"
                        style={{ width: `${percent}%` }}
                      />
                    </div>
                    <p className="text-[10px] tabular-nums text-muted-foreground">
                      {run.steps_completed}/{run.steps_total} steps
                    </p>
                  </div>
                ) : (
                  <p className="mt-2 text-[10px] text-muted-foreground">
                    Heartbeat {ageLabel(slot.heartbeat_age_seconds)} ago
                  </p>
                )}

                {slot.last_error ? (
                  <p className="mt-2 line-clamp-2 text-[10px] text-rose-700 dark:text-rose-300">
                    {slot.last_error}
                  </p>
                ) : null}
              </article>
            );
          })}
        </div>
      )}

      {pool.recent_failures.length > 0 ? (
        <details className="rounded-md border border-rose-500/20 px-3 py-2">
          <summary className="cursor-pointer text-xs text-rose-700 dark:text-rose-300">
            Recent worker incidents ({pool.recent_failures.length})
          </summary>
          <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
            {pool.recent_failures.map((failure) => (
              <li key={failure.id}>
                <span className="font-medium text-foreground">{failure.event_type}</span>: {failure.message}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
