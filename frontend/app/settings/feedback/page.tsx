"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Merge, RotateCcw, Sparkles, UploadCloud, X } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
// v0.9.0: Discord + GitHub-push setup cards moved to /settings/integrations
// (the new generic 3rd-party app hub). The send-event paths still route
// through whatever integration rows the admin configures there; this page
// just shows feedback + the Push to GitHub action.
import { Textarea } from "@/components/ui/textarea";
import {
  applyRoadmapRankings,
  combineRoadmapSuggestions,
  createRoadmapSuggestion,
  decideRoadmapSuggestion,
  deleteRoadmapSuggestion,
  detectRoadmapCombines,
  pushRoadmapToGitHub,
  rankRoadmapSuggestions,
  reEvaluateRoadmapSuggestion,
  setRoadmapSuggestionCompletion,
  setRoadmapSuggestionPriority,
} from "@/lib/api";
import { qk, useMe, useRoadmapSuggestions } from "@/lib/hooks";
import { runSlugFromId, useRunToast } from "@/components/run-toast-provider";
import type {
  BulkRankResponse,
  CombineDetectResponse,
  Me,
  RankedRowRead,
  RoadmapListFilters,
  RoadmapSuggestion,
  RoadmapSuggestionStatus,
} from "@/lib/types";

type PriorityBucket = "all" | "1-3" | "4-6" | "7-10" | "unranked";

const PRIORITY_BUCKETS: PriorityBucket[] = ["all", "1-3", "4-6", "7-10", "unranked"];

function bucketToFilters(b: PriorityBucket): {
  priority_min?: number;
  priority_max?: number;
  include_unranked?: boolean;
} {
  // "unranked" fetches everything and filters client-side (backend has
  // no explicit "priority IS NULL only" mode; range params would need
  // a value outside 1-10 which we reject at validation).
  if (b === "all" || b === "unranked") return { include_unranked: true };
  if (b === "1-3") return { priority_min: 1, priority_max: 3, include_unranked: false };
  if (b === "4-6") return { priority_min: 4, priority_max: 6, include_unranked: false };
  return { priority_min: 7, priority_max: 10, include_unranked: false };
}

function priorityChipClass(p: number | null): string {
  if (p === null) return "border-slate-500/40 bg-slate-500/10 text-slate-700 dark:text-slate-200";
  if (p <= 3) return "border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-200";
  if (p <= 6) return "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-200";
  return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200";
}

// Tenant-global feedback surface. Any authenticated analyst drops in a product
// idea; the planner agent emits pros/cons; an admin approves or rejects.
// Approved items export to ROADMAP.md for Claude Code to pick up as PR work.

type FilterChip = "all" | RoadmapSuggestionStatus;

const STATUS_LABEL: Record<RoadmapSuggestionStatus, string> = {
  pending_review: "Pending",
  approved: "Approved",
  rejected: "Rejected",
};

const STATUS_CLASS: Record<RoadmapSuggestionStatus, string> = {
  pending_review:
    "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-200",
  approved: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
  rejected: "border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-200",
};

export default function SettingsFeedbackPage() {
  // v1.0.0: react-query owns the me + rows queries. reload() maps to
  // invalidateQueries on the current filter combo — the many mutation
  // handlers below still use the same call site, keeping the diff small.
  const qc = useQueryClient();
  const { data: me } = useMe();
  const runToast = useRunToast();
  const [filter, setFilter] = useState<FilterChip>("all");
  const [priorityBucket, setPriorityBucket] = useState<PriorityBucket>("all");
  const [showCombined, setShowCombined] = useState(false);
  const [sortByPriority, setSortByPriority] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [body, setBody] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [pushStatus, setPushStatus] = useState<string | null>(null);
  const [combineBusy, setCombineBusy] = useState(false);
  const [combineResult, setCombineResult] =
    useState<CombineDetectResponse | null>(null);
  const [rankBusy, setRankBusy] = useState(false);
  const [rankResult, setRankResult] = useState<BulkRankResponse | null>(null);

  const listFilters: RoadmapListFilters = useMemo(() => {
    const p = bucketToFilters(priorityBucket);
    return { ...p, show_combined: showCombined };
  }, [priorityBucket, showCombined]);

  const rowsQuery = useRoadmapSuggestions(listFilters);
  const rows = rowsQuery.data ?? null;
  const error =
    localError ??
    (rowsQuery.error instanceof Error
      ? rowsQuery.error.message
      : rowsQuery.error
        ? String(rowsQuery.error)
        : null);
  const setError = setLocalError;

  const reload = useCallback(async () => {
    setLocalError(null);
    await qc.invalidateQueries({
      queryKey: qk.roadmapSuggestions(listFilters),
    });
  }, [qc, listFilters]);

  const visible = useMemo(() => {
    if (!rows) return null;
    let out = filter === "all" ? rows : rows.filter((r) => r.status === filter);
    if (priorityBucket === "unranked") {
      out = out.filter((r) => r.priority === null);
    }
    if (sortByPriority) {
      out = [...out].sort((a, b) => {
        // 1..10 first, then unranked at the end. 1 is highest.
        if (a.priority === null && b.priority === null) return 0;
        if (a.priority === null) return 1;
        if (b.priority === null) return -1;
        return a.priority - b.priority;
      });
    }
    return out;
  }, [rows, filter, sortByPriority]);

  const onSetPriority = useCallback(
    async (id: string, priority: number | null) => {
      try {
        await setRoadmapSuggestionPriority(id, priority);
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [reload],
  );

  const onDetectCombines = useCallback(async () => {
    setCombineBusy(true);
    setError(null);
    try {
      const res = await detectRoadmapCombines();
      setCombineResult(res);
      if (res.execution_id) {
        runToast.fire({
          kind: "planner",
          runSlug: runSlugFromId(res.execution_id),
          label: "Combine detection complete",
          sublabel: `${res.clusters.length} cluster(s) proposed`,
          openHref: `/settings/agent-runs?run=${res.execution_id}`,
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCombineBusy(false);
    }
  }, [runToast]);

  const onConfirmMerge = useCallback(
    async (primaryId: string, memberIds: string[]) => {
      try {
        await combineRoadmapSuggestions(primaryId, memberIds);
        await reload();
        // Drop the applied cluster from the modal so the analyst can
        // still merge remaining clusters without re-running detect.
        setCombineResult((prev) =>
          prev
            ? {
                ...prev,
                clusters: prev.clusters.filter(
                  (c) => c.primary_id !== primaryId,
                ),
              }
            : prev,
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [reload],
  );

  const onDetectRank = useCallback(async () => {
    setRankBusy(true);
    setError(null);
    try {
      const res = await rankRoadmapSuggestions();
      setRankResult(res);
      if (res.execution_id) {
        runToast.fire({
          kind: "planner",
          runSlug: runSlugFromId(res.execution_id),
          label: "Prioritization complete",
          sublabel: `${res.rankings.length} row(s) ranked`,
          openHref: `/settings/agent-runs?run=${res.execution_id}`,
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRankBusy(false);
    }
  }, [runToast]);

  const onApplyRank = useCallback(
    async (rankings: RankedRowRead[]) => {
      try {
        await applyRoadmapRankings(rankings);
        setRankResult(null);
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [reload],
  );

  const onSubmit = useCallback(async () => {
    const text = body.trim();
    if (text.length < 4) {
      setError("Suggestion is too short.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await createRoadmapSuggestion({ body: text });
      setBody("");
      await reload();
    } catch (err) {
      // The backend returns 409 when an open/approved row already has this
      // body verbatim (v0.8 dedup). Surface a friendly message instead of
      // the raw "409 Conflict: {detail: …}" the generic request() helper
      // produces. We don't have a typed error layer yet, so parse from the
      // message string.
      const raw = err instanceof Error ? err.message : String(err);
      const isDupe = raw.startsWith("409");
      if (isDupe) {
        const match = raw.match(/\{.*\}$/);
        if (match) {
          try {
            const parsed = JSON.parse(match[0]) as {
              detail?: { message?: string; existing_status?: string };
            };
            const friendly = parsed.detail?.message;
            if (friendly) {
              setError(friendly);
              return;
            }
          } catch {
            // fall through to raw
          }
        }
        setError(
          "A suggestion with this exact body already exists. Check the list below.",
        );
        return;
      }
      setError(raw);
    } finally {
      setSubmitting(false);
    }
  }, [body, reload]);

  const onDecide = useCallback(
    async (
      row: RoadmapSuggestion,
      decision: "approved" | "rejected",
    ) => {
      const note = window.prompt(
        `Optional note for ${decision === "approved" ? "approving" : "rejecting"}:`,
        row.review_note ?? "",
      );
      if (note === null) return;
      try {
        await decideRoadmapSuggestion(row.id, {
          status: decision,
          note: note || null,
        });
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [reload],
  );

  const onReEvaluate = useCallback(
    async (row: RoadmapSuggestion) => {
      try {
        const updated = await reEvaluateRoadmapSuggestion(row.id);
        await reload();
        if (updated.agent_execution_id) {
          runToast.fire({
            kind: "planner",
            runSlug: runSlugFromId(updated.agent_execution_id),
            label: "AI feedback re-evaluated",
            sublabel: updated.agent_summary?.slice(0, 80) ?? undefined,
            openHref: `/settings/agent-runs?run=${updated.agent_execution_id}`,
          });
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [reload, runToast],
  );

  const onSetCompletion = useCallback(
    async (row: RoadmapSuggestion, completed: boolean) => {
      if (
        completed &&
        !window.confirm(
          "Mark this feedback as shipped? It'll move to the Shipped section of ROADMAP.md.",
        )
      ) {
        return;
      }
      try {
        await setRoadmapSuggestionCompletion(row.id, completed);
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [reload],
  );

  const onDelete = useCallback(
    async (row: RoadmapSuggestion) => {
      if (
        !window.confirm("Delete this feedback entry? This can't be undone.")
      ) {
        return;
      }
      try {
        await deleteRoadmapSuggestion(row.id);
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [reload],
  );

  const onPushToGitHub = useCallback(async () => {
    setPushing(true);
    setError(null);
    setPushStatus(null);
    try {
      const result = await pushRoadmapToGitHub();
      const short = result.commit_sha ? result.commit_sha.slice(0, 7) : "ok";
      setPushStatus(
        `Pushed to ${result.owner}/${result.repo}@${result.branch}:${result.path} (commit ${short}).`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPushing(false);
    }
  }, []);

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-6">
      <div>
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← engagements
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          Feedback
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Drop in product ideas, scope adjustments, or anything you want the
          team to consider. A planning agent reads your entry against the
          project charter and current handoff, then writes pros and cons.
          Admins approve or reject; approved items export to{" "}
          <code className="text-foreground">ROADMAP.md</code> so Claude Code
          can pick them up as future PR work.
        </p>
      </div>

      {me?.role !== "guest" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">New feedback</CardTitle>
            <CardDescription>
              Be specific — the agent gives a better read when the idea
              names the user-visible behavior, the phase or area it
              touches, and any constraints.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="e.g. Add a 'starred findings' filter so I can pin a short shortlist while I write the report."
              rows={5}
              disabled={submitting}
            />
            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">
                {submitting
                  ? "Agent is evaluating…"
                  : `${body.trim().length} characters`}
              </p>
              <Button
                onClick={onSubmit}
                disabled={submitting || body.trim().length < 4}
              >
                {submitting ? "Submitting…" : "Submit for review"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {me?.role === "guest" && (
        <Card>
          <CardContent className="py-4 text-sm text-muted-foreground">
            Your role is <strong className="text-foreground">guest</strong>{" "}
            — you can read feedback but can&apos;t submit new entries. Ask an
            admin to upgrade you.
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">All feedback</CardTitle>
              <CardDescription>
                Newest first. Approved items land in the export.
              </CardDescription>
            </div>
            {me?.is_admin && (
              <Button
                variant="outline"
                size="sm"
                onClick={onPushToGitHub}
                disabled={pushing}
                title="Commit the rendered ROADMAP.md (approved feedback) to GitHub via the configured integration below."
              >
                <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
                {pushing ? "Pushing…" : "Push to GitHub"}
              </Button>
            )}
          </div>
          {pushStatus && (
            <p className="mt-2 text-xs text-muted-foreground">{pushStatus}</p>
          )}
          <div className="mt-3 flex flex-wrap gap-1.5">
            {(["all", "pending_review", "approved", "rejected"] as const).map(
              (chip) => (
                <button
                  key={chip}
                  onClick={() => setFilter(chip)}
                  className={`rounded-full border px-3 py-0.5 text-xs transition ${
                    filter === chip
                      ? "border-foreground bg-foreground text-background"
                      : "border-border text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {chip === "all" ? "All" : STATUS_LABEL[chip]}
                </button>
              ),
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {error && <p className="text-sm text-critical">{error}</p>}
          {visible === null && !error && (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
          {visible !== null && visible.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {filter === "all"
                ? "No feedback yet — submit the first one above."
                : `No ${STATUS_LABEL[filter as RoadmapSuggestionStatus].toLowerCase()} feedback.`}
            </p>
          )}
          {/* v0.16.0 priority filter chips + agent ops */}
          {me?.role !== "guest" && (
            <div className="flex flex-wrap items-center gap-2 border-b border-border/40 pb-3">
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                Priority:
              </span>
              {PRIORITY_BUCKETS.map((b) => (
                <button
                  key={b}
                  onClick={() => setPriorityBucket(b)}
                  className={`rounded-full border px-2.5 py-0.5 text-xs transition ${
                    priorityBucket === b
                      ? "border-foreground bg-foreground text-background"
                      : "border-border text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {b === "all" ? "All" : b === "unranked" ? "Unranked" : b}
                </button>
              ))}
              <button
                onClick={() => setSortByPriority((v) => !v)}
                className={`ml-2 rounded-full border px-2.5 py-0.5 text-xs transition ${
                  sortByPriority
                    ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-800 dark:text-emerald-100"
                    : "border-border text-muted-foreground hover:text-foreground"
                }`}
              >
                Sort by priority
              </button>
              <label className="ml-2 flex items-center gap-1 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={showCombined}
                  onChange={(e) => setShowCombined(e.target.checked)}
                  className="accent-emerald-500"
                />
                Show combined
              </label>
              <div className="ml-auto flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={combineBusy}
                  onClick={onDetectCombines}
                  title="Ask the planner to propose merge clusters across the open pool. Nothing merges until you confirm each one."
                >
                  <Merge className="mr-1.5 h-3.5 w-3.5" />
                  {combineBusy ? "Detecting…" : "Combine (agent)"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={rankBusy}
                  onClick={onDetectRank}
                  title="Ask the planner to bulk-rank the open pool. Preview first — nothing changes until you apply."
                >
                  <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                  {rankBusy ? "Ranking…" : "Prioritize (agent)"}
                </Button>
              </div>
            </div>
          )}

          {visible?.map((row) => (
            <SuggestionRow
              key={row.id}
              row={row}
              me={me ?? null}
              onDecide={onDecide}
              onDelete={onDelete}
              onReEvaluate={onReEvaluate}
              onSetPriority={onSetPriority}
              onSetCompletion={onSetCompletion}
            />
          ))}
        </CardContent>
      </Card>

      {combineResult && (
        <CombineReviewModal
          result={combineResult}
          rows={rows}
          onConfirm={onConfirmMerge}
          onClose={() => setCombineResult(null)}
        />
      )}

      {rankResult && (
        <RankApplyModal
          result={rankResult}
          rows={rows}
          onApply={onApplyRank}
          onClose={() => setRankResult(null)}
        />
      )}
    </div>
  );
}

function CombineReviewModal({
  result,
  rows,
  onConfirm,
  onClose,
}: {
  result: CombineDetectResponse;
  rows: RoadmapSuggestion[] | null;
  onConfirm: (primaryId: string, memberIds: string[]) => Promise<void>;
  onClose: () => void;
}) {
  const rowById = useMemo(() => {
    const map = new Map<string, RoadmapSuggestion>();
    (rows ?? []).forEach((r) => map.set(r.id, r));
    return map;
  }, [rows]);

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/70" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[min(720px,92vw)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-lg border border-border bg-popover p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 pb-3">
          <div>
            <h3 className="text-sm font-semibold">Proposed merges</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Planner reviewed {result.pool_size} open suggestions and proposed{" "}
              {result.clusters.length} cluster
              {result.clusters.length === 1 ? "" : "s"}. Each merge preserves
              audit (rows are marked combined, not deleted).
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
        <div className="flex-1 overflow-y-auto pr-1">
          {result.error && (
            <p className="rounded-md border border-critical/40 bg-critical/5 p-3 text-xs text-critical">
              {result.error}
            </p>
          )}
          {result.clusters.length === 0 && !result.error && (
            <p className="text-xs text-muted-foreground">
              No merge candidates found in the open pool.
            </p>
          )}
          <ul className="space-y-3">
            {result.clusters.map((c) => {
              const primary = rowById.get(c.primary_id);
              const members = c.member_ids
                .map((id) => rowById.get(id))
                .filter((r): r is RoadmapSuggestion => r !== undefined);
              return (
                <li
                  key={c.primary_id}
                  className="rounded-md border border-border/60 bg-background p-3"
                >
                  <p className="text-xs text-muted-foreground">
                    {c.reasoning}
                  </p>
                  <div className="mt-2 rounded-md border border-emerald-500/40 bg-emerald-500/5 p-2 text-xs">
                    <p className="mb-1 text-[10px] uppercase tracking-wide text-emerald-600 dark:text-emerald-300">
                      Survivor
                    </p>
                    <p className="whitespace-pre-wrap text-foreground">
                      {primary?.body ?? "(row not in current filter)"}
                    </p>
                  </div>
                  {members.map((m) => (
                    <div
                      key={m.id}
                      className="mt-1.5 rounded-md border border-rose-500/40 bg-rose-500/5 p-2 text-xs"
                    >
                      <p className="mb-1 text-[10px] uppercase tracking-wide text-rose-600 dark:text-rose-300">
                        Fold into survivor
                      </p>
                      <p className="whitespace-pre-wrap text-foreground">
                        {m.body}
                      </p>
                    </div>
                  ))}
                  <div className="mt-2 flex justify-end">
                    <Button
                      size="sm"
                      onClick={() => onConfirm(c.primary_id, c.member_ids)}
                    >
                      Merge {members.length + 1} into 1
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
        <p className="mt-2 text-[10px] text-muted-foreground/70">
          {result.model} · {result.tokens_in}in / {result.tokens_out}out
        </p>
      </div>
    </>
  );
}

function RankApplyModal({
  result,
  rows,
  onApply,
  onClose,
}: {
  result: BulkRankResponse;
  rows: RoadmapSuggestion[] | null;
  onApply: (rankings: RankedRowRead[]) => Promise<void>;
  onClose: () => void;
}) {
  const rowById = useMemo(() => {
    const map = new Map<string, RoadmapSuggestion>();
    (rows ?? []).forEach((r) => map.set(r.id, r));
    return map;
  }, [rows]);

  const sorted = useMemo(
    () => [...result.rankings].sort((a, b) => a.priority - b.priority),
    [result.rankings],
  );

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/70" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[min(720px,92vw)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-lg border border-border bg-popover p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 pb-3">
          <div>
            <h3 className="text-sm font-semibold">Apply agent ranking?</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Planner reviewed {result.pool_size} open suggestions and produced
              priorities 1-10 (1 = highest). Applying overwrites the{" "}
              <strong>priority</strong> field on every row named below. Rows
              not named keep their current priority.
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
        <div className="flex-1 overflow-y-auto pr-1">
          {result.error && (
            <p className="rounded-md border border-critical/40 bg-critical/5 p-3 text-xs text-critical">
              {result.error}
            </p>
          )}
          <ul className="space-y-1.5">
            {sorted.map((r) => {
              const row = rowById.get(r.id);
              return (
                <li
                  key={r.id}
                  className="flex items-start gap-3 rounded-md border border-border/60 bg-background p-2"
                >
                  <span
                    className={`shrink-0 rounded border px-2 py-0.5 text-[11px] font-mono tabular-nums ${priorityChipClass(
                      r.priority,
                    )}`}
                  >
                    {r.priority}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="line-clamp-2 text-xs text-foreground">
                      {row?.body ?? "(row not in current filter)"}
                    </p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">
                      {r.reasoning}
                    </p>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
        <div className="mt-3 flex items-center justify-between">
          <p className="text-[10px] text-muted-foreground/70">
            {result.model} · {result.tokens_in}in / {result.tokens_out}out
          </p>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button size="sm" onClick={() => onApply(result.rankings)}>
              Apply {result.rankings.length} priorities
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

function SuggestionRow({
  row,
  me,
  onDecide,
  onDelete,
  onReEvaluate,
  onSetPriority,
  onSetCompletion,
}: {
  row: RoadmapSuggestion;
  me: Me | null;
  onDecide: (
    row: RoadmapSuggestion,
    decision: "approved" | "rejected",
  ) => void;
  onDelete: (row: RoadmapSuggestion) => void;
  onReEvaluate: (row: RoadmapSuggestion) => void;
  onSetPriority: (id: string, priority: number | null) => Promise<void>;
  onSetCompletion: (
    row: RoadmapSuggestion,
    completed: boolean,
  ) => Promise<void>;
}) {
  const isAdmin = me?.is_admin ?? false;
  const isGuest = me?.role === "guest";
  const isAuthor = me?.id !== undefined && row.author_user_id === me.id;
  const canDelete =
    isAdmin || (isAuthor && row.status === "pending_review");
  const [reEvaluating, setReEvaluating] = useState(false);

  const handleReEvaluate = async () => {
    setReEvaluating(true);
    try {
      await onReEvaluate(row);
    } finally {
      setReEvaluating(false);
    }
  };

  const evaluating =
    row.agent_summary === null &&
    row.agent_pros.length === 0 &&
    row.agent_cons.length === 0;

  const isShipped = row.implemented_at !== null;
  return (
    <div className="rounded-md border border-border bg-card/40 p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <p className="whitespace-pre-wrap text-foreground">{row.body}</p>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span
            className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${
              STATUS_CLASS[row.status]
            }`}
          >
            {STATUS_LABEL[row.status]}
          </span>
          {isShipped && (
            <span
              className="rounded-full border border-violet-500/40 bg-violet-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-violet-700 dark:text-violet-200"
              title={`Marked shipped ${new Date(row.implemented_at as string).toLocaleString()}`}
            >
              <CheckCircle2 className="-mt-0.5 mr-1 inline-block h-3 w-3" />
              Shipped
            </span>
          )}
          <span
            className={`rounded-full border px-2 py-0.5 text-[10px] font-mono tabular-nums ${priorityChipClass(
              row.priority,
            )}`}
            title="Priority (1 = highest, 10 = lowest). Null = unranked."
          >
            {row.priority !== null ? `P${row.priority}` : "unranked"}
          </span>
        </div>
      </div>
      {row.combined_into_id && (
        <p className="mt-1 text-[10px] text-muted-foreground/70">
          ↳ merged into {row.combined_into_id.slice(0, 8)}
        </p>
      )}

      {row.agent_summary && (
        <p className="mt-2 text-xs italic text-muted-foreground">
          {row.agent_summary}
        </p>
      )}

      {evaluating && (
        <p className="mt-2 text-xs text-muted-foreground">
          Agent evaluation failed or is still in progress. You can still
          approve or reject manually.
        </p>
      )}

      {(row.agent_pros.length > 0 || row.agent_cons.length > 0) && (
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {row.agent_pros.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-emerald-600 dark:text-emerald-300">
                Pros
              </p>
              <ul className="mt-1 list-disc pl-4 text-xs text-muted-foreground">
                {row.agent_pros.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}
          {row.agent_cons.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-rose-600 dark:text-rose-300">
                Cons
              </p>
              <ul className="mt-1 list-disc pl-4 text-xs text-muted-foreground">
                {row.agent_cons.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {row.review_note && (
        <p className="mt-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Admin note:</span>{" "}
          {row.review_note}
        </p>
      )}

      <div className="mt-3 flex items-center justify-between gap-2">
        <p className="text-[10px] text-muted-foreground">
          submitted {new Date(row.created_at).toLocaleString()}
          {(row.author_display_name || row.author_email) && (
            <> by {row.author_display_name || row.author_email}</>
          )}
          {row.reviewed_at && (
            <>
              {" "}· reviewed {new Date(row.reviewed_at).toLocaleString()}
              {(row.reviewed_by_display_name || row.reviewed_by_email) && (
                <> by {row.reviewed_by_display_name || row.reviewed_by_email}</>
              )}
            </>
          )}
          {row.implemented_at && (
            <>
              {" "}· shipped {new Date(row.implemented_at).toLocaleString()}
              {(row.implemented_by_display_name ||
                row.implemented_by_email) && (
                <>
                  {" "}by{" "}
                  {row.implemented_by_display_name ||
                    row.implemented_by_email}
                </>
              )}
            </>
          )}
        </p>
        <div className="flex items-center gap-2">
          {!isGuest && (
            <select
              value={row.priority ?? ""}
              onChange={(e) =>
                onSetPriority(
                  row.id,
                  e.target.value === "" ? null : Number(e.target.value),
                )
              }
              className="h-7 rounded-md border border-border bg-background px-1.5 text-xs"
              title="Set priority (1 = highest)"
            >
              <option value="">unranked</option>
              {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  P{n}
                </option>
              ))}
            </select>
          )}
          {!isGuest && (
            <Button
              size="sm"
              variant="ghost"
              onClick={handleReEvaluate}
              disabled={reEvaluating}
              title="Re-run the planner agent on this entry — useful if the first eval failed or the project context has shifted."
              className="text-muted-foreground hover:text-foreground"
            >
              {reEvaluating ? "Re-running…" : "AI Feedback"}
            </Button>
          )}
          {isAdmin && row.status === "pending_review" && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onDecide(row, "approved")}
              >
                Approve
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onDecide(row, "rejected")}
              >
                Reject
              </Button>
            </>
          )}
          {isAdmin && row.status === "approved" && !isShipped && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onSetCompletion(row, true)}
              title="Mark this feedback as shipped. It moves to the Shipped section of ROADMAP.md."
              className="border-violet-500/40 text-violet-800 dark:text-violet-100 hover:bg-violet-500/10"
            >
              <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
              Mark completed
            </Button>
          )}
          {isAdmin && isShipped && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onSetCompletion(row, false)}
              title="Reopen — clears the shipped timestamp and moves this row back to the Open section."
              className="text-muted-foreground hover:text-foreground"
            >
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
              Reopen
            </Button>
          )}
          {canDelete && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onDelete(row)}
              className="text-muted-foreground hover:text-critical"
            >
              Delete
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
