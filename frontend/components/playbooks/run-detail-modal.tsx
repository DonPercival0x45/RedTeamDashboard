"use client";

// v3 Track A — playbook run detail. Shows the run's status + counts +
// timing + approval attribution, plus context-sensitive action buttons:
//   awaiting_approval → Approve + Reject (with reason)
//   pending / running → Cancel
//   terminal          → no actions
//
// Polls via usePlaybookRun so status transitions land promptly.

import { useState } from "react";
import { Loader2 } from "lucide-react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  useApprovePlaybookRunMutation,
  useCancelPlaybookRunMutation,
  usePlaybookRun,
  useRejectPlaybookRunMutation,
} from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { PlaybookRunStatus } from "@/lib/types";

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

function fieldRow(label: string, value: React.ReactNode) {
  return (
    <div className="grid grid-cols-3 gap-2 text-xs">
      <div className="text-muted-foreground">{label}</div>
      <div className="col-span-2 break-all">{value}</div>
    </div>
  );
}

export function RunDetailModal({
  runId,
  onClose,
}: {
  runId: string;
  onClose: () => void;
}) {
  const query = usePlaybookRun(runId);
  const approve = useApprovePlaybookRunMutation();
  const reject = useRejectPlaybookRunMutation();
  const cancel = useCancelPlaybookRunMutation();
  const [rejectionReason, setRejectionReason] = useState("");
  const [approvalReason, setApprovalReason] = useState("");
  const [mode, setMode] = useState<"view" | "reject">("view");
  const [error, setError] = useState<string | null>(null);

  const run = query.data;

  const doApprove = async () => {
    if (!run) return;
    setError(null);
    try {
      await approve.mutateAsync({
        runId: run.id,
        reason: approvalReason.trim() || undefined,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to approve.");
    }
  };

  const doReject = async () => {
    if (!run) return;
    if (!rejectionReason.trim()) {
      setError("Reason is required to reject.");
      return;
    }
    setError(null);
    try {
      await reject.mutateAsync({
        runId: run.id,
        reason: rejectionReason.trim(),
      });
      setMode("view");
      setRejectionReason("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to reject.");
    }
  };

  const doCancel = async () => {
    if (!run) return;
    if (!confirm("Cancel this run?")) return;
    setError(null);
    try {
      await cancel.mutateAsync(run.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to cancel.");
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Playbook run</DialogTitle>
        </DialogHeader>
        {query.isLoading || !run ? (
          <p className="text-sm text-muted-foreground flex items-center gap-2">
            <Loader2 className="h-3 w-3 animate-spin" /> Loading run…
          </p>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <Badge
                variant="outline"
                className={cn("text-xs", STATUS_BADGE[run.status])}
              >
                {STATUS_LABEL[run.status]}
              </Badge>
              <span className="text-sm font-medium">
                {run.playbook_slug}{" "}
                <span className="text-xs text-muted-foreground">
                  v{run.playbook_version}
                </span>
              </span>
            </div>

            <div className="space-y-2 rounded-md border border-border p-3">
              {fieldRow("Executor", run.executor.toUpperCase())}
              {fieldRow(
                "Scope",
                Array.isArray(run.scope_subset)
                  ? run.scope_subset.map((s) => String(s)).join(", ") || "—"
                  : "—",
              )}
              {fieldRow(
                "Steps",
                `${run.steps_succeeded} succeeded · ${run.steps_failed} failed · ${run.steps_total} total`,
              )}
              {fieldRow(
                "Findings",
                `${run.findings_total} total · ${run.findings_new} new · ${run.findings_high_severity} high`,
              )}
              {fieldRow(
                "Started",
                run.started_at
                  ? new Date(run.started_at).toLocaleString()
                  : "—",
              )}
              {fieldRow(
                "Completed",
                run.completed_at
                  ? new Date(run.completed_at).toLocaleString()
                  : "—",
              )}
              {run.last_error
                ? fieldRow(
                    "Last error",
                    <span className="text-rose-600 dark:text-rose-400">
                      {run.last_error}
                    </span>,
                  )
                : null}
              {run.approved_by
                ? fieldRow(
                    "Approved",
                    <span>
                      by {run.approved_by.slice(0, 8)}… at{" "}
                      {run.approved_at
                        ? new Date(run.approved_at).toLocaleString()
                        : "—"}
                      {run.approval_reason ? ` — ${run.approval_reason}` : ""}
                    </span>,
                  )
                : null}
              {run.rejected_by
                ? fieldRow(
                    "Rejected",
                    <span>
                      by {run.rejected_by.slice(0, 8)}… at{" "}
                      {run.rejected_at
                        ? new Date(run.rejected_at).toLocaleString()
                        : "—"}
                      {run.rejection_reason ? ` — ${run.rejection_reason}` : ""}
                    </span>,
                  )
                : null}
            </div>

            {run.status === "awaiting_approval" && mode === "reject" ? (
              <div className="space-y-2">
                <Textarea
                  value={rejectionReason}
                  onChange={(e) => setRejectionReason(e.target.value)}
                  placeholder="Reason for rejecting this run"
                  className="min-h-[4rem] text-xs"
                />
              </div>
            ) : null}
            {run.status === "awaiting_approval" && mode === "view" ? (
              <div className="space-y-2">
                <Textarea
                  value={approvalReason}
                  onChange={(e) => setApprovalReason(e.target.value)}
                  placeholder="Approval reason (optional)"
                  className="min-h-[3rem] text-xs"
                />
              </div>
            ) : null}

            {error ? (
              <p className="text-xs text-rose-600 dark:text-rose-400">
                {error}
              </p>
            ) : null}
          </div>
        )}
        <DialogFooter>
          {run && run.status === "awaiting_approval" ? (
            mode === "reject" ? (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setMode("view");
                    setError(null);
                  }}
                >
                  Back
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={doReject}
                  disabled={reject.isPending}
                >
                  {reject.isPending ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  ) : null}
                  Confirm reject
                </Button>
              </>
            ) : (
              <>
                <DialogClose asChild>
                  <Button variant="outline" size="sm">
                    Close
                  </Button>
                </DialogClose>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => {
                    setMode("reject");
                    setError(null);
                  }}
                >
                  Reject
                </Button>
                <Button
                  size="sm"
                  onClick={doApprove}
                  disabled={approve.isPending}
                >
                  {approve.isPending ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  ) : null}
                  Approve
                </Button>
              </>
            )
          ) : run &&
            (run.status === "pending" || run.status === "running") ? (
            <>
              <DialogClose asChild>
                <Button variant="outline" size="sm">
                  Close
                </Button>
              </DialogClose>
              <Button
                variant="destructive"
                size="sm"
                onClick={doCancel}
                disabled={cancel.isPending}
              >
                {cancel.isPending ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                ) : null}
                Cancel run
              </Button>
            </>
          ) : (
            <DialogClose asChild>
              <Button size="sm">Close</Button>
            </DialogClose>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
