"use client";

// v3 Track A — playbook run detail. Shows the run's status + counts +
// timing + approval attribution, plus context-sensitive action buttons:
//   awaiting_approval → Approve + Reject (with reason)
//   pending / running → Cancel
//   terminal          → no actions
//
// Polls via usePlaybookRun so status transitions land promptly.

import { useRef, useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { QueryState } from "@/components/query-state";
import {
  useApprovePlaybookRunMutation,
  useCancelPlaybookRunMutation,
  useEvidenceArtifact,
  usePlaybookRun,
  useRejectPlaybookRunMutation,
} from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type {
  PlaybookRunStatus,
  PlaybookStepExecutionRead,
  PlaybookStepExecutionStatus,
} from "@/lib/types";

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

const STEP_STATUS_LABEL: Record<PlaybookStepExecutionStatus, string> = {
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  stub: "Stub — no collection",
  cancelled: "Cancelled",
};

const STEP_STATUS_BADGE: Record<PlaybookStepExecutionStatus, string> = {
  running: "border-blue-500/40 text-blue-700 dark:text-blue-300",
  succeeded: "border-emerald-500/40 text-emerald-700 dark:text-emerald-300",
  failed: "border-rose-500/40 text-rose-700 dark:text-rose-300",
  stub: "border-amber-500/40 text-amber-700 dark:text-amber-300",
  cancelled: "border-zinc-500/40 text-muted-foreground",
};

function formatDuration(durationMs: number | null) {
  if (durationMs === null) return "—";
  if (durationMs < 1000) return `${durationMs} ms`;
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1)} s`;
  return `${Math.floor(durationMs / 60_000)}m ${Math.round((durationMs % 60_000) / 1000)}s`;
}

function EvidenceContent({
  artifactId,
  engagementSlug,
}: {
  artifactId: string;
  engagementSlug: string;
}) {
  const evidence = useEvidenceArtifact(artifactId);
  return (
    <div className="mt-2">
      <QueryState
        isLoading={evidence.isLoading}
        error={evidence.error}
        hasData={!!evidence.data}
        compact
        loadingLabel="Loading evidence…"
        errorLabel="Could not load this evidence artifact."
        onRetry={() => void evidence.refetch()}
        isRetrying={evidence.isFetching}
      />
      {evidence.data ? (
        <div className="space-y-1.5">
          <p className="text-[11px] text-muted-foreground">
            Redacted JSON · {evidence.data.size_bytes.toLocaleString()} bytes
            {evidence.data.truncated ? " · stored preview is truncated" : ""}
          </p>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-muted p-2 text-[11px]">
            {JSON.stringify(evidence.data.payload, null, 2)}
          </pre>
          <p className="break-all font-mono text-[10px] text-muted-foreground">
            SHA-256 {evidence.data.sha256}
          </p>
          {evidence.data.finding_id && engagementSlug ? (
            <Link
              className="inline-flex rounded-sm text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              href={`/e/findings/${evidence.data.finding_id}?slug=${encodeURIComponent(engagementSlug)}`}
            >
              Open canonical finding
            </Link>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function EvidenceDisclosure({
  receipt,
  engagementSlug,
}: {
  receipt: PlaybookStepExecutionRead;
  engagementSlug: string;
}) {
  const [open, setOpen] = useState(false);
  if (!receipt.evidence) return null;

  return (
    <div className="mt-2 border-t border-border/60 pt-2">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-7 px-1 text-xs"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? (
          <ChevronDown className="mr-1 h-3 w-3" />
        ) : (
          <ChevronRight className="mr-1 h-3 w-3" />
        )}
        {open ? "Hide evidence" : "View evidence"}
      </Button>
      {open ? (
        <EvidenceContent
          artifactId={receipt.evidence.id}
          engagementSlug={engagementSlug}
        />
      ) : null}
    </div>
  );
}

function StepReceipts({
  receipts,
  engagementSlug,
}: {
  receipts: PlaybookStepExecutionRead[];
  engagementSlug: string;
}) {
  return (
    <section aria-labelledby="step-receipts-heading" className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 id="step-receipts-heading" className="text-sm font-medium">
          Step receipts
        </h3>
        <span className="text-xs text-muted-foreground">
          {receipts.length} attempts
        </span>
      </div>
      {receipts.length === 0 ? (
        <p className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
          No step receipts recorded yet. Queued and legacy runs may not have
          per-target execution history.
        </p>
      ) : (
        <ol className="max-h-[28rem] space-y-2 overflow-y-auto pr-1">
          {receipts.map((receipt, index) => (
            <li key={receipt.id} className="rounded-md border border-border p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <h4 className="break-all text-xs font-medium">
                    Execution {index + 1}: {receipt.tool_slug}
                  </h4>
                  <p className="break-all text-xs text-muted-foreground">
                    {receipt.target}
                  </p>
                </div>
                <Badge
                  variant="outline"
                  className={cn("text-[10px]", STEP_STATUS_BADGE[receipt.status])}
                >
                  {STEP_STATUS_LABEL[receipt.status]}
                </Badge>
              </div>
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] sm:grid-cols-3">
                <div>
                  <dt className="text-muted-foreground">Transport</dt>
                  <dd>{receipt.transport}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Attempt</dt>
                  <dd>{receipt.attempt}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Duration</dt>
                  <dd>{formatDuration(receipt.duration_ms)}</dd>
                </div>
              </dl>
              {receipt.error ? (
                <p className="mt-2 break-words text-xs text-rose-600 dark:text-rose-400">
                  {receipt.error}
                </p>
              ) : null}
              <EvidenceDisclosure
                receipt={receipt}
                engagementSlug={engagementSlug}
              />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function fieldRow(label: string, value: React.ReactNode) {
  return (
    <div className="grid grid-cols-3 gap-2 text-xs">
      <div className="text-muted-foreground">{label}</div>
      <div className="col-span-2 break-all">{value}</div>
    </div>
  );
}

export function RunDetailPanel({
  runId,
  onClose,
  canWrite = true,
  className,
}: {
  runId: string;
  onClose: () => void;
  canWrite?: boolean;
  className?: string;
}) {
  const query = usePlaybookRun(runId);
  const approve = useApprovePlaybookRunMutation();
  const reject = useRejectPlaybookRunMutation();
  const cancel = useCancelPlaybookRunMutation();
  const [rejectionReason, setRejectionReason] = useState("");
  const [approvalReason, setApprovalReason] = useState("");
  const [mode, setMode] = useState<"view" | "reject" | "cancel">("view");
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
    setError(null);
    try {
      await cancel.mutateAsync(run.id);
      setMode("view");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to cancel.");
    }
  };

  return (
    <div className={cn("space-y-4", className)}>
      <div>
        <h2 className="text-lg font-semibold leading-none tracking-tight">Manage run</h2>
        <p className="mt-1.5 text-sm text-muted-foreground">
          Review lifecycle, findings, targets, and any required decision.
        </p>
      </div>
        {!run ? (
          <QueryState
            isLoading={query.isLoading}
            error={query.error}
            loadingLabel="Loading run…"
            errorLabel="Could not load this playbook run."
            onRetry={() => void query.refetch()}
            isRetrying={query.isFetching}
          />
        ) : (
          <div className="space-y-4">
            <QueryState
              isLoading={false}
              error={query.error}
              hasData
              compact
              onRetry={() => void query.refetch()}
              isRetrying={query.isFetching}
            />
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

            <StepReceipts
              receipts={run.step_executions ?? []}
              engagementSlug={run.engagement_slug}
            />

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
            {(run.status === "pending" || run.status === "running") &&
            mode === "cancel" ? (
              <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                Cancel this playbook run? Completed steps and persisted findings
                remain available; unfinished steps will not run.
              </p>
            ) : null}

            {error ? (
              <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">
                {error}
              </p>
            ) : null}
          </div>
        )}
        <DialogFooter>
          {run && canWrite && run.status === "awaiting_approval" ? (
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
                <Button variant="outline" size="sm" onClick={onClose}>
                  Close
                </Button>
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
            canWrite &&
            (run.status === "pending" || run.status === "running") ? (
            mode === "cancel" ? (
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
                  autoFocus
                  variant="destructive"
                  size="sm"
                  onClick={doCancel}
                  disabled={cancel.isPending}
                >
                  {cancel.isPending ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  ) : null}
                  Confirm cancel
                </Button>
              </>
            ) : (
              <>
                <Button variant="outline" size="sm" onClick={onClose}>
                  Close
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => {
                    setMode("cancel");
                    setError(null);
                  }}
                >
                  Cancel run
                </Button>
              </>
            )
          ) : (
            <Button size="sm" onClick={onClose}>Close</Button>
          )}
        </DialogFooter>
    </div>
  );
}

export function RunDetailModal({
  runId,
  onClose,
  returnFocus,
  canWrite = true,
}: {
  runId: string;
  onClose: () => void;
  returnFocus?: () => void;
  canWrite?: boolean;
}) {
  const openerRef = useRef<HTMLElement | null>(
    typeof document === "undefined"
      ? null
      : (document.activeElement as HTMLElement | null),
  );

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="max-h-[calc(100dvh-2rem)] max-w-3xl overflow-y-auto"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (returnFocus) returnFocus();
          else openerRef.current?.focus();
        }}
      >
        <DialogHeader className="sr-only">
          <DialogTitle>Manage playbook run</DialogTitle>
          <DialogDescription>
            Review lifecycle, findings, targets, and any required decision.
          </DialogDescription>
        </DialogHeader>
        <RunDetailPanel runId={runId} onClose={onClose} canWrite={canWrite} />
      </DialogContent>
    </Dialog>
  );
}
