"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock3, RotateCcw, ShieldAlert } from "lucide-react";
import {
  createFindingRemediationUpdate,
  createFindingRetest,
  getFindingFollowUp,
} from "@/lib/api";
import { qk } from "@/lib/hooks";
import type {
  FindingRemediationStatus,
  FindingRetestOutcome,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const REMEDIATION_LABELS: Record<FindingRemediationStatus, string> = {
  acknowledged: "Acknowledged",
  in_progress: "In progress",
  ready_for_retest: "Ready for retest",
  client_reports_fixed: "Client reports fixed",
  accepted_risk: "Risk accepted",
};

const RETEST_LABELS: Record<FindingRetestOutcome, string> = {
  fixed: "Fixed",
  partially_fixed: "Partially fixed",
  not_fixed: "Not fixed",
  inconclusive: "Inconclusive",
};

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function FindingFollowUpPanel({ findingId }: { findingId: string }) {
  const qc = useQueryClient();
  const [remediationStatus, setRemediationStatus] =
    useState<FindingRemediationStatus>("acknowledged");
  const [remediationNote, setRemediationNote] = useState("");
  const [retestOutcome, setRetestOutcome] =
    useState<FindingRetestOutcome>("inconclusive");
  const [retestNote, setRetestNote] = useState("");
  const queryKey = ["finding-follow-up", findingId] as const;
  const followUp = useQuery({
    queryKey,
    queryFn: () => getFindingFollowUp(findingId),
  });
  const refresh = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey }),
      qc.invalidateQueries({ queryKey: qk.findingActivity(findingId) }),
    ]);
  };
  const remediationMutation = useMutation({
    mutationFn: () =>
      createFindingRemediationUpdate(findingId, {
        status: remediationStatus,
        note: remediationNote.trim() || null,
      }),
    onSuccess: async () => {
      setRemediationNote("");
      await refresh();
    },
  });
  const retestMutation = useMutation({
    mutationFn: () =>
      createFindingRetest(findingId, {
        outcome: retestOutcome,
        note: retestNote.trim() || null,
      }),
    onSuccess: async () => {
      setRetestNote("");
      await refresh();
    },
  });
  const history = useMemo(() => {
    if (!followUp.data) return [];
    return [
      ...followUp.data.remediation_updates.map((row) => ({
        id: `remediation:${row.id}`,
        at: row.reported_at,
        kind: "Client update" as const,
        label: REMEDIATION_LABELS[row.status],
        note: row.note,
      })),
      ...followUp.data.retests.map((row) => ({
        id: `retest:${row.id}`,
        at: row.tested_at,
        kind: "Analyst retest" as const,
        label: RETEST_LABELS[row.outcome],
        note: row.note,
      })),
    ].sort((a, b) => b.at.localeCompare(a.at));
  }, [followUp.data]);

  if (followUp.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading follow-up history…</p>;
  }
  if (followUp.error) {
    return (
      <div className="rounded-lg border border-destructive/40 p-4 text-sm text-destructive">
        Could not load remediation history. {followUp.error instanceof Error ? followUp.error.message : ""}
        <Button variant="outline" size="sm" className="ml-3" onClick={() => void followUp.refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-border bg-card/40 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Current client status
          </p>
          <p className="mt-2 flex items-center gap-2 text-sm font-semibold">
            <Clock3 className="h-4 w-4" />
            {followUp.data?.latest_remediation
              ? REMEDIATION_LABELS[followUp.data.latest_remediation.status]
              : "No update recorded"}
          </p>
        </div>
        <div className="rounded-lg border border-border bg-card/40 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Latest analyst retest
          </p>
          <p className="mt-2 flex items-center gap-2 text-sm font-semibold">
            <CheckCircle2 className="h-4 w-4" />
            {followUp.data?.latest_retest
              ? RETEST_LABELS[followUp.data.latest_retest.outcome]
              : "No retest recorded"}
          </p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <form
          className="space-y-3 rounded-lg border border-border p-4"
          onSubmit={(event) => {
            event.preventDefault();
            remediationMutation.mutate();
          }}
        >
          <div>
            <h3 className="text-sm font-medium">Record client update</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Track what the client or system owner reports. This does not change validation.
            </p>
          </div>
          <label className="block text-xs font-medium">
            Status
            <select
              aria-label="Client remediation status"
              value={remediationStatus}
              onChange={(event) =>
                setRemediationStatus(event.target.value as FindingRemediationStatus)
              }
              className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              {Object.entries(REMEDIATION_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium">
            Note
            <textarea
              aria-label="Client remediation note"
              value={remediationNote}
              onChange={(event) => setRemediationNote(event.target.value)}
              placeholder="What changed, who reported it, and what should happen next?"
              maxLength={10_000}
              className="mt-1 min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
          </label>
          {remediationMutation.error && (
            <p className="text-xs text-destructive">
              {remediationMutation.error instanceof Error
                ? remediationMutation.error.message
                : "Could not record update."}
            </p>
          )}
          <Button type="submit" size="sm" disabled={remediationMutation.isPending}>
            Record update
          </Button>
        </form>

        <form
          className="space-y-3 rounded-lg border border-border p-4"
          onSubmit={(event) => {
            event.preventDefault();
            retestMutation.mutate();
          }}
        >
          <div>
            <h3 className="text-sm font-medium">Record retest result</h3>
            <p className="mt-1 flex gap-1 text-xs text-muted-foreground">
              <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Records work already performed. It does not run a tool or replay an execution.
            </p>
          </div>
          <label className="block text-xs font-medium">
            Outcome
            <select
              aria-label="Retest outcome"
              value={retestOutcome}
              onChange={(event) =>
                setRetestOutcome(event.target.value as FindingRetestOutcome)
              }
              className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              {Object.entries(RETEST_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium">
            Evidence note
            <textarea
              aria-label="Retest evidence note"
              value={retestNote}
              onChange={(event) => setRetestNote(event.target.value)}
              placeholder="What was tested, what evidence was observed, and what remains?"
              maxLength={10_000}
              className="mt-1 min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
          </label>
          {retestMutation.error && (
            <p className="text-xs text-destructive">
              {retestMutation.error instanceof Error
                ? retestMutation.error.message
                : "Could not record retest."}
            </p>
          )}
          <Button type="submit" size="sm" disabled={retestMutation.isPending}>
            Record retest
          </Button>
        </form>
      </div>

      <section className="rounded-lg border border-border p-4">
        <div className="flex items-center gap-2">
          <RotateCcw className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-sm font-medium">Follow-up history</h3>
          <Badge variant="outline">{history.length}</Badge>
        </div>
        {history.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">
            No remediation or retest activity has been recorded.
          </p>
        ) : (
          <ol className="mt-3 max-h-[28rem] space-y-3 overflow-y-auto pr-2">
            {history.map((row) => (
              <li key={row.id} className="rounded-md border border-border/70 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary">{row.kind}</Badge>
                    <span className="text-sm font-medium">{row.label}</span>
                  </div>
                  <time className="text-xs text-muted-foreground">{formatDate(row.at)}</time>
                </div>
                {row.note && <p className="mt-2 whitespace-pre-wrap text-sm">{row.note}</p>}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
