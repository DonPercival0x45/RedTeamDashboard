"use client";

// v3 Track A — analyst-facing Playbooks tab on the engagement Automation page.
//
// Two sections stacked:
//   1. Catalog — cards showing every playbook; "Kick run" button per card.
//   2. Runs — table of runs for this engagement (newest first) with status
//      pills + action affordances (approve/reject/cancel/view). Polls every
//      3s while anything is running/pending/awaiting; 15s otherwise.

import { useState } from "react";
import { Play, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { KickRunModal } from "@/components/playbooks/kick-run-modal";
import { RunDetailModal } from "@/components/playbooks/run-detail-modal";
import { usePlaybooks, usePlaybookRuns } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { PlaybookRead, PlaybookRunRead, PlaybookRunStatus } from "@/lib/types";

const STATUS_BADGE: Record<PlaybookRunStatus, string> = {
  awaiting_approval: "border-amber-500/40 text-amber-700 dark:text-amber-300",
  pending: "border-sky-500/40 text-sky-700 dark:text-sky-300",
  running: "border-blue-500/40 text-blue-700 dark:text-blue-300",
  completed: "border-emerald-500/40 text-emerald-700 dark:text-emerald-300",
  partial: "border-amber-500/40 text-amber-700 dark:text-amber-300",
  failed: "border-rose-500/40 text-rose-700 dark:text-rose-300",
  cancelled: "border-zinc-500/40 text-muted-foreground",
};

const STATUS_LABEL: Record<PlaybookRunStatus, string> = {
  awaiting_approval: "Awaiting approval",
  pending: "Pending",
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  failed: "Failed",
  cancelled: "Cancelled",
};

function StatusBadge({ status }: { status: PlaybookRunStatus }) {
  return (
    <Badge variant="outline" className={cn("text-xs", STATUS_BADGE[status])}>
      {STATUS_LABEL[status]}
    </Badge>
  );
}

function PlaybookCard({
  playbook,
  onKick,
}: {
  playbook: PlaybookRead;
  onKick: (pb: PlaybookRead) => void;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h4 className="font-medium text-sm">{playbook.name}</h4>
            <span className="text-xs text-muted-foreground">
              v{playbook.version}
            </span>
            {playbook.active ? (
              <Badge
                variant="outline"
                className="border-amber-500/40 text-amber-700 dark:text-amber-300 text-[10px] px-1.5 py-0"
              >
                Gated
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 text-xs text-muted-foreground line-clamp-2">
            {playbook.description || "No description."}
          </p>
        </div>
      </div>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {playbook.step_count} steps · {playbook.applies_to_asset_class}
        </span>
        <Button size="sm" onClick={() => onKick(playbook)}>
          <Play className="mr-1 h-3 w-3" />
          Kick run
        </Button>
      </div>
    </div>
  );
}

function RunRow({
  run,
  onOpen,
}: {
  run: PlaybookRunRead;
  onOpen: (r: PlaybookRunRead) => void;
}) {
  const scope = Array.isArray(run.scope_subset)
    ? run.scope_subset.map((s) => String(s)).join(", ")
    : "";
  const started = run.started_at
    ? new Date(run.started_at).toLocaleString()
    : "—";
  return (
    <tr
      className="hover:bg-muted/40 cursor-pointer"
      onClick={() => onOpen(run)}
    >
      <td className="px-3 py-2">
        <StatusBadge status={run.status} />
      </td>
      <td className="px-3 py-2 text-sm">
        {run.playbook_slug}{" "}
        <span className="text-xs text-muted-foreground">
          v{run.playbook_version}
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground uppercase">
        {run.executor}
      </td>
      <td className="px-3 py-2 text-xs">
        <span className="line-clamp-1 max-w-[16rem]">{scope || "—"}</span>
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">
        {run.steps_succeeded}/{run.steps_total}
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">{started}</td>
    </tr>
  );
}

export function PlaybooksTab({ engagementSlug }: { engagementSlug: string }) {
  const playbooksQuery = usePlaybooks();
  const runsQuery = usePlaybookRuns(engagementSlug);
  const [kickPlaybook, setKickPlaybook] = useState<PlaybookRead | null>(null);
  const [openRun, setOpenRun] = useState<PlaybookRunRead | null>(null);

  const catalog = playbooksQuery.data ?? [];
  const runs = runsQuery.data ?? [];
  const awaiting = runs.filter((r) => r.status === "awaiting_approval");

  return (
    <div className="space-y-6">
      {awaiting.length > 0 ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
          <span className="font-medium">
            {awaiting.length} run{awaiting.length === 1 ? "" : "s"} awaiting
            approval
          </span>
          <span className="ml-2 text-xs text-muted-foreground">
            Click a row below to review.
          </span>
        </div>
      ) : null}

      <section>
        <h3 className="text-sm font-semibold mb-3">Catalog</h3>
        {playbooksQuery.isLoading ? (
          <p className="text-sm text-muted-foreground flex items-center gap-2">
            <Loader2 className="h-3 w-3 animate-spin" /> Loading playbooks…
          </p>
        ) : catalog.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No playbooks in the catalog yet.
          </p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {catalog.map((pb) => (
              <PlaybookCard key={pb.id} playbook={pb} onKick={setKickPlaybook} />
            ))}
          </div>
        )}
      </section>

      <section>
        <h3 className="text-sm font-semibold mb-3">Runs</h3>
        {runsQuery.isLoading ? (
          <p className="text-sm text-muted-foreground flex items-center gap-2">
            <Loader2 className="h-3 w-3 animate-spin" /> Loading runs…
          </p>
        ) : runs.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No playbook runs on this engagement yet.
          </p>
        ) : (
          <div className="overflow-hidden rounded-lg border border-border">
            <table className="w-full text-left">
              <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Playbook</th>
                  <th className="px-3 py-2">Executor</th>
                  <th className="px-3 py-2">Scope</th>
                  <th className="px-3 py-2">Steps</th>
                  <th className="px-3 py-2">Started</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {runs.map((run) => (
                  <RunRow key={run.id} run={run} onOpen={setOpenRun} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {kickPlaybook ? (
        <KickRunModal
          engagementSlug={engagementSlug}
          playbook={kickPlaybook}
          onClose={() => setKickPlaybook(null)}
        />
      ) : null}
      {openRun ? (
        <RunDetailModal
          runId={openRun.id}
          onClose={() => setOpenRun(null)}
        />
      ) : null}
    </div>
  );
}
