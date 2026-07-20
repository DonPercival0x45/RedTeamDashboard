"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { DateTime } from "@/components/date-time";
import {
  acceptSuggestion,
  ApiError,
  dismissSuggestion,
  listOrchestratorTools,
  listScope,
  listSuggestions,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { StrategyMarkdown } from "@/components/strategy-markdown";
import {
  approveCompletion,
  blockWorkItem,
  cancelObjective,
  createCheckpoint,
  createCoverageItem,
  createExecutionSuggestion,
  createObjective,
  createStrategyRevision,
  createWorkItem,
  decideStrategyRevision,
  decideStrategySignal,
  getCompletionReadiness,
  getCurrentStrategy,
  getResumeBriefing,
  getStrategistChat,
  listCheckpoints,
  listCompletionDecisions,
  listCoverage,
  listObjectives,
  listStrategyRevisions,
  listStrategySignals,
  listWorkItems,
  postStrategistChat,
  reopenCompletion,
  resolveWorkItem,
  runEngagementStrategist,
  resetStrategyWorkspace,
  startCompletionReview,
  summarizeStrategistChat,
  clearStrategistChat,
  transitionObjective,
  transitionWorkItem,
  updateCoverageItem,
  updateObjective,
  updateWorkItem,
} from "@/lib/strategy-api";
import type {
  StrategistChatState,
  StrategistRunResponse,
} from "@/lib/strategist-types";
import type {
  Checkpoint,
  CompletionDecision,
  CompletionException,
  CompletionReadiness,
  CoverageItem,
  CoverageStatus,
  Objective,
  ObjectivePriority,
  ObjectiveStatus,
  ResumeBriefing,
  ResumeRecordRef,
  StrategyRevision,
  StrategySignal,
  WorkItem,
  WorkItemExecutor,
  WorkItemPriority,
  WorkItemResolution,
  WorkItemStatus,
} from "@/lib/strategy-types";
import type {
  EngagementStatus,
  OrchestratorTool,
  ScopeItem,
  Suggestion,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type LoadState = {
  current: StrategyRevision | null;
  revisions: StrategyRevision[];
  objectives: Objective[];
  workItems: WorkItem[];
  signals: StrategySignal[];
  checkpoints: Checkpoint[];
  coverage: CoverageItem[];
  completion: CompletionReadiness;
  decisions: CompletionDecision[];
  resume: ResumeBriefing;
  chat: StrategistChatState;
  suggestions: Suggestion[];
  scope: ScopeItem[];
  tools: OrchestratorTool[];
};

const WORK_STATUSES: Array<WorkItemStatus | "all"> = [
  "all",
  "ready",
  "in_progress",
  "blocked",
  "deferred",
  "completed",
  "cancelled",
];
const PRIORITIES: WorkItemPriority[] = ["critical", "high", "medium", "low"];
const EXECUTORS: WorkItemExecutor[] = [
  "unassigned",
  "analyst",
  "finding_agent",
  "engagement_strategist",
  "tactical",
];
const RESOLUTIONS: WorkItemResolution[] = [
  "completed",
  "disproved",
  "not_applicable",
  "duplicate",
  "superseded",
  "unable_to_complete",
];
const COVERAGE_STATUSES: CoverageStatus[] = [
  "not_started",
  "planned",
  "active",
  "covered",
  "blocked",
  "deferred",
  "accepted_gap",
  "not_applicable",
];
const COVERAGE_CATEGORIES = [
  "scope_review",
  "asset_discovery",
  "service_identification",
  "scanner_coverage",
  "finding_review",
  "evidence_collection",
  "reporting",
];
const STRATEGY_SECTIONS = [
  "Situation and constraints",
  "Priorities and hypotheses",
  "Execution approach",
  "Coverage and completion criteria",
];

function emptyStrategySections(): Record<string, string> {
  return Object.fromEntries(STRATEGY_SECTIONS.map((section) => [section, ""]));
}

const SLICE_LABELS: Record<string, string> = {
  current: "current strategy",
  revisions: "revisions",
  signals: "signals",
  completion: "completion readiness",
  resume: "resume briefing",
  chat: "strategist chat",
  suggestions: "proposals",
  scope: "formal scope",
  tools: "execution tool catalog",
  objectives: "objectives",
  workItems: "work queue",
  checkpoints: "checkpoints",
  coverage: "coverage",
  decisions: "completion decisions",
};

// A fully-shaped empty state so the view can render incrementally as slices
// arrive. `data` is never null after first render, which removes the
// all-or-nothing failure mode where one flaky endpoint blanks the workspace.
function emptyLoadState(): LoadState {
  return {
    current: null,
    revisions: [],
    objectives: [],
    workItems: [],
    signals: [],
    checkpoints: [],
    coverage: [],
    completion: {
      work_state: "active",
      work_state_version: 0,
      ready: false,
      readiness_hash: "",
      checks: [],
      accepted_gap_candidates: [],
      generated_at: "",
    },
    decisions: [],
    resume: {
      current_focus: {},
      since_checkpoint: {},
      active_work: [],
      blocked_work: [],
      decisions_required: [],
      recommended_starting_records: [],
      coverage_summary: {},
      report_readiness: {},
      generated_at: "",
      current_tasks: [],
      recent_findings: [],
      recently_closed: [],
      recent_activity: [],
    },
    chat: { conversation_id: null, messages: [] },
    suggestions: [],
    scope: [],
    tools: [],
  };
}

function SliceErrorBanner({ failed, onRetry }: { failed: string[]; onRetry: () => void }) {
  if (failed.length === 0) return null;
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-200">
      <span>Some sections failed to load ({failed.map((k) => SLICE_LABELS[k] ?? k).join(", ")}). Other sections are still usable.</span>
      <button type="button" onClick={onRetry} className="rounded border border-amber-500/40 px-2 py-1 text-xs hover:bg-amber-500/10">Retry</button>
    </div>
  );
}

export function StrategyView({
  slug,
  engagementStatus,
}: {
  slug: string;
  engagementStatus: EngagementStatus;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const requestedWorkItemId = searchParams?.get("workItem") ?? null;
  const showInitialGuidancePath =
    searchParams?.get("setup") === "initial-guidance";
  const handledWorkLink = useRef<string | null>(null);
  // Monotonic refresh sequence: only the latest refresh's data lands, so a slow
  // or stale response can never overwrite newer state.
  const seqRef = useRef(0);
  // Mirror of `busy` so the background poll can skip an in-flight mutation
  // without re-subscribing its interval.
  const busyRef = useRef<string | null>(null);
  const [data, setData] = useState<LoadState>(emptyLoadState());
  const [bootstrapLoading, setBootstrapLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [sliceErrors, setSliceErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [strategyBody, setStrategyBody] = useState("");
  const [strategySummary, setStrategySummary] = useState("");
  const [strategyFlyoutOpen, setStrategyFlyoutOpen] = useState(false);
  const [strategyFlyoutEditing, setStrategyFlyoutEditing] = useState(false);
  const [strategySectionDrafts, setStrategySectionDrafts] = useState(emptyStrategySections);
  const [objectiveTitle, setObjectiveTitle] = useState("");
  const [objectivePriority, setObjectivePriority] = useState<ObjectivePriority>("medium");
  const [workTitle, setWorkTitle] = useState("");
  const [workPriority, setWorkPriority] = useState<WorkItemPriority>("medium");
  const [workExecutor, setWorkExecutor] = useState<WorkItemExecutor>("unassigned");
  const [workObjective, setWorkObjective] = useState("");
  const [workStatusFilter, setWorkStatusFilter] = useState<WorkItemStatus | "all">("all");
  const [workQuery, setWorkQuery] = useState("");
  const [selectedWorkId, setSelectedWorkId] = useState<string | null>(requestedWorkItemId);
  const [workFlyoutOpen, setWorkFlyoutOpen] = useState(false);
  // Flyout close ALSO strips ?workItem= from the URL. Without this, the
  // deep-link param persists and a refresh (or a tab switch back into
  // Strategy) re-opens the flyout — same "reopens forever" pattern the
  // ?run= fix established in v2.5.x.
  const handleWorkFlyoutOpenChange = useCallback(
    (next: boolean) => {
      setWorkFlyoutOpen(next);
      if (!next && searchParams?.get("workItem")) {
        const p = new URLSearchParams(searchParams.toString());
        p.delete("workItem");
        const qs = p.toString();
        router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
      }
    },
    [pathname, router, searchParams],
  );
  const [tab, setTab] = useState(requestedWorkItemId ? "work" : "strategy");
  const [checkpointNarrative, setCheckpointNarrative] = useState("");
  const [coverageTarget, setCoverageTarget] = useState("");
  const [coverageKind, setCoverageKind] = useState("domain");
  const [coverageCategory, setCoverageCategory] = useState("scope_review");
  const [completionExceptions, setCompletionExceptions] = useState<
    Record<string, string>
  >({});
  const [strategistMessage, setStrategistMessage] = useState("");
  const [lastStrategistRun, setLastStrategistRun] =
    useState<StrategistRunResponse | null>(null);

  const setSliceError = useCallback((key: string, message: string | null) => {
    setSliceErrors((prev) => {
      if (message === null && !(key in prev)) return prev;
      const next = { ...prev };
      if (message === null) delete next[key];
      else next[key] = message;
      return next;
    });
  }, []);

  // Load one slice independently. A failure records a per-slice error and
  // never aborts the other slices, so a single flaky endpoint can't blank the
  // whole workspace. Stale results (an older refresh still resolving) are
  // discarded via the sequence guard.
  const loadSlice = useCallback(
    async function <K extends keyof LoadState>(
      key: K,
      seq: number,
      loader: () => Promise<LoadState[K]>,
    ): Promise<void> {
      try {
        const value = await loader();
        if (seqRef.current !== seq) return;
        setData((prev) => ({ ...prev, [key]: value }) as LoadState);
        setSliceError(key, null);
      } catch (reason) {
        if (seqRef.current !== seq) return;
        setSliceError(key, messageFor(reason));
      }
    },
    [setSliceError],
  );

  const refresh = useCallback(async (): Promise<void> => {
    setError(null);
    const seq = ++seqRef.current;
    // Core slices always load in parallel and never block each other.
    void loadSlice("revisions", seq, () => listStrategyRevisions(slug));
    void loadSlice("signals", seq, () => listStrategySignals(slug));
    void loadSlice("completion", seq, () => getCompletionReadiness(slug));
    void loadSlice("resume", seq, () => getResumeBriefing(slug));
    void loadSlice("chat", seq, () => getStrategistChat(slug));
    void loadSlice("suggestions", seq, () => listSuggestions(slug, "open"));
    void loadSlice("scope", seq, () => listScope(slug));
    void loadSlice("tools", seq, () => listOrchestratorTools());
    // The current strategy gates the workspace slice, so await it inline.
    try {
      const current = await getCurrentStrategy(slug);
      if (seqRef.current !== seq) return;
      setData((prev) => ({ ...prev, current }) as LoadState);
      setSliceError("current", null);
      if (current) {
        setStrategyBody((previous) => previous || current.body || "");
        setStrategySummary((previous) => previous || current.summary || "");
        void loadSlice("objectives", seq, () => listObjectives(slug));
        void loadSlice("workItems", seq, () => listWorkItems(slug));
        void loadSlice("checkpoints", seq, () => listCheckpoints(slug));
        void loadSlice("coverage", seq, () => listCoverage(slug));
        void loadSlice("decisions", seq, () => listCompletionDecisions(slug));
      } else {
        setData((prev) => ({
          ...prev,
          current: null,
          objectives: [],
          workItems: [],
          checkpoints: [],
          coverage: [],
          decisions: [],
        }) as LoadState);
        setStrategyBody("");
        setStrategySummary("");
      }
    } catch (reason) {
      if (seqRef.current !== seq) return;
      setSliceError("current", messageFor(reason));
    }
  }, [loadSlice, setSliceError, slug]);

  useEffect(() => {
    if (requestedWorkItemId) {
      setSelectedWorkId(requestedWorkItemId);
      setWorkFlyoutOpen(true);
      setTab("work");
    }
  }, [requestedWorkItemId]);

  useEffect(() => {
    if (!requestedWorkItemId || handledWorkLink.current === requestedWorkItemId || !data.workItems.some((item) => item.id === requestedWorkItemId)) return;
    handledWorkLink.current = requestedWorkItemId;
    const frame = window.requestAnimationFrame(() => {
      const detail = document.getElementById("work-item-detail");
      detail?.focus({ preventScroll: true });
      detail?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [data.workItems, requestedWorkItemId]);

  // Validate the current selection against the latest work items (keep a valid
  // id, else fall back to the first item). Moved out of refresh() so it also
  // fires when the work queue is reloaded by an independent slice load.
  useEffect(() => {
    setSelectedWorkId((previous) =>
      previous && data.workItems.some((item) => item.id === previous)
        ? previous
        : data.workItems[0]?.id ?? null,
    );
  }, [data.workItems]);

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  // Reset stale cross-engagement state on slug change and bootstrap the view.
  // Replaces the previous `key={slug}` full remount (which discarded local
  // drafts on every engagement switch).
  useEffect(() => {
    let active = true;
    setData(emptyLoadState());
    setSliceErrors({});
    setError(null);
    setNotice(null);
    setBootstrapLoading(true);
    refresh()
      .catch((reason) => {
        if (active) setError(messageFor(reason));
      })
      .finally(() => {
        if (active) setBootstrapLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refresh]);

  // Shared strategy/work state can change in another analyst session. Until
  // typed SSE events ship, refresh on focus and at a modest visible-page tick.
  // The poll yields while a mutation is in flight so it never races one.
  useEffect(() => {
    const refetch = () => {
      if (busyRef.current) return;
      if (document.visibilityState === "visible") void refresh().catch(() => undefined);
    };
    window.addEventListener("focus", refetch);
    const interval = window.setInterval(refetch, 30_000);
    return () => {
      window.removeEventListener("focus", refetch);
      window.clearInterval(interval);
    };
  }, [refresh]);

  const mutate = useCallback(
    async (key: string, action: () => Promise<unknown>, success: string) => {
      setBusy(key);
      busyRef.current = key;
      setError(null);
      setNotice(null);
      try {
        await action();
        await refresh();
        setNotice(success);
        return true;
      } catch (reason) {
        // CAS (row_version) conflicts are common in multi-analyst sessions.
        // Surface a targeted message and auto-refresh instead of a generic error.
        if (reason instanceof ApiError && reason.status === 409) {
          setError("This item changed in another session — refreshed automatically. Try again if needed.");
          await refresh().catch(() => undefined);
        } else {
          setError(messageFor(reason));
          // A batched action may have partially succeeded. Refresh before
          // returning so successful decisions disappear while failures remain.
          await refresh().catch(() => undefined);
        }
        return false;
      } finally {
        setBusy(null);
        busyRef.current = null;
      }
    },
    [refresh],
  );

  const selectedWork = useMemo(
    () => data?.workItems.find((item) => item.id === selectedWorkId) ?? null,
    [data?.workItems, selectedWorkId],
  );
  const visibleWork = useMemo(() => {
    const query = workQuery.trim().toLowerCase();
    return (data?.workItems ?? []).filter(
      (item) =>
        (workStatusFilter === "all" || item.status === workStatusFilter) &&
        (!query ||
          `${item.title} ${item.description ?? ""} ${item.rationale ?? ""}`
            .toLowerCase()
            .includes(query)),
    );
  }, [data?.workItems, workQuery, workStatusFilter]);

  if (bootstrapLoading) {
    return <p className="text-sm text-muted-foreground">Loading engagement strategy…</p>;
  }

  // Completion review is a live remediation state: analysts must be able to
  // resolve blockers and refresh the readiness hash before approval.
  const readOnly =
    engagementStatus !== "active" || data.completion.work_state === "completed";
  const remaining = data.workItems.filter((item) =>
    ["ready", "in_progress", "blocked"].includes(item.status),
  ).length;
  const blocked = data.workItems.filter((item) => item.status === "blocked").length;
  const deferred = data.workItems.filter((item) => item.status === "deferred").length;
  const openSignals = data.signals.filter((signal) => signal.status === "open");
  const proposedRevisions = data.revisions.filter((revision) => revision.state === "proposed");
  const strategyRequired = !data.current;
  const openSuggestions = strategyRequired
    ? data.suggestions.filter((suggestion) => suggestion.kind === "strategy_revision")
    : data.suggestions;
  const decisionCount = openSignals.length + openSuggestions.length + proposedRevisions.length;
  const failedSlices = Object.keys(sliceErrors);
  const currentStrategyError = sliceErrors.current;

  if (!data.current && currentStrategyError) {
    return (
      <div className="space-y-6">
        <StrategyHeader busy={busy} refresh={refresh} />
        <SliceErrorBanner
          failed={failedSlices.filter((slice) => slice !== "current")}
          onRetry={() => void refresh()}
        />
        {readOnly && <ReadOnlyNotice engagementStatus={engagementStatus} />}
        <section role="alert" className="rounded-md border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-800 dark:text-amber-200">
          <h3 className="font-semibold">Could not confirm the current strategy</h3>
          <p className="mt-1">
            {currentStrategyError} Initial-strategy creation is unavailable until this check succeeds.
          </p>
          <Button
            size="sm"
            variant="outline"
            className="mt-3"
            disabled={busy !== null}
            onClick={() => void refresh()}
          >
            Retry current strategy
          </Button>
        </section>
        {error && <p role="alert" className="rounded-md border border-critical/40 bg-critical/10 p-3 text-sm text-critical">{error}</p>}
      </div>
    );
  }

  if (strategyRequired) {
    return (
      <div className="space-y-6">
        <StrategyHeader busy={busy} refresh={refresh} />
        <SliceErrorBanner failed={failedSlices} onRetry={() => void refresh()} />
        {readOnly && <ReadOnlyNotice engagementStatus={engagementStatus} />}
        {error && <p role="alert" className="rounded-md border border-critical/40 bg-critical/10 p-3 text-sm text-critical">{error}</p>}
        {notice && <p role="status" className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-200">{notice}</p>}
        {showInitialGuidancePath && (
          <p role="status" className="rounded-md border border-violet-500/40 bg-violet-500/10 p-3 text-sm text-violet-800 dark:text-violet-100">
            Scope saved. Choose <strong>Generate initial guidance</strong> below to create reviewable proposals from the engagement&apos;s actual scope. Nothing is accepted or run automatically.
          </p>
        )}
        <StrategistSection slug={slug} readOnly={readOnly} chat={data.chat} message={strategistMessage} setMessage={setStrategistMessage} lastRun={lastStrategistRun} setLastRun={setLastStrategistRun} busy={busy} mutate={mutate} hasCurrentStrategy={false} />
        {decisionCount > 0 && <NeedsDecisionSection slug={slug} readOnly={readOnly} openSignals={[]} openSuggestions={openSuggestions} proposedRevisions={proposedRevisions} currentRevisionId={null} busy={busy} mutate={mutate} />}
        <InitialStrategyBuilder slug={slug} readOnly={readOnly} summary={strategySummary} setSummary={setStrategySummary} sections={strategySectionDrafts} setSections={setStrategySectionDrafts} busy={busy} mutate={mutate} />
        <StrategyRequiredGate findingCount={Number(data.resume.current_focus.finding_count ?? 0)} />
        <ResumeSection resume={data.resume} slug={slug} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <StrategyHeader busy={busy} refresh={refresh} />
      <SliceErrorBanner failed={failedSlices} onRetry={() => void refresh()} />

      {readOnly && <ReadOnlyNotice engagementStatus={engagementStatus} />}
      {error && <p role="alert" className="rounded-md border border-critical/40 bg-critical/10 p-3 text-sm text-critical">{error}</p>}
      {notice && <p role="status" className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-200">{notice}</p>}

      {decisionCount > 0 && <NeedsDecisionSection slug={slug} readOnly={readOnly} openSignals={openSignals} openSuggestions={openSuggestions} proposedRevisions={proposedRevisions} currentRevisionId={data.current?.id ?? null} busy={busy} mutate={mutate} />}

      <Tabs value={tab} onValueChange={setTab} className="space-y-4">
        {/* Sticky top chrome: analytics + tab bar persist at the top of the
            scroll region (<main overflow-y-auto>) across every tab, so the
            at-a-glance counts and tab navigation stay visible while scrolling. */}
        <div className="sticky top-0 z-10 border-b border-border bg-background py-3">
          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <Metric label="Strategy" value={data.current ? `v${data.current.version}` : "Not set"} />
            <Metric label="Remaining work" value={String(remaining)} />
            <Metric label="Blocked" value={String(blocked)} tone={blocked ? "warn" : undefined} />
            <Metric label="Deferred" value={String(deferred)} />
            <Metric label="Decisions" value={String(decisionCount)} />
          </section>
          <TabsList className="mt-3 border-b-0">
            <TabsTrigger value="strategy">Strategy</TabsTrigger>
            <TabsTrigger value="objectives">Objectives</TabsTrigger>
            <TabsTrigger value="work">Work queue</TabsTrigger>
            <TabsTrigger value="coverage">Coverage &amp; Readiness</TabsTrigger>
            <TabsTrigger value="strategist">Strategist</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="strategy" className="space-y-6">
      <ResumeSection resume={data.resume} slug={slug} />

      <section className="rounded-lg border border-border bg-card/40 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">Current strategy</h3>
            <p className="text-xs text-muted-foreground">
              {data.current ? `Version ${data.current.version} · ${data.current.state}` : "Create the first shared revision."}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {data.current && <DateTime value={data.current.updated_at} />}
            <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => { setStrategyFlyoutEditing(false); setStrategyFlyoutOpen(true); }}>Expand ↗</Button>
            {!readOnly && data.current && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy !== null}
                onClick={() => {
                  if (window.confirm("Reset the current strategy and seeded workspace so you can regenerate it?")) {
                    void mutate(
                      "strategy-reset",
                      () => resetStrategyWorkspace(slug),
                      "Strategy reset. Generate or create a new initial strategy.",
                    );
                  }
                }}
              >
                {busy === "strategy-reset" ? "Resetting…" : "Reset strategy"}
              </Button>
            )}
          </div>
        </div>
        <Input
          value={strategySummary}
          onChange={(event) => setStrategySummary(event.target.value)}
          placeholder="Revision summary"
          disabled={readOnly}
          className="mt-3"
        />
        <Textarea
          value={strategyBody}
          onChange={(event) => setStrategyBody(event.target.value)}
          placeholder="Mission, hypotheses, priorities, constraints, coverage expectations, and exit criteria…"
          rows={10}
          disabled={readOnly}
          className="mt-2"
        />
        <div className="mt-3 flex justify-end">
          <Button
            size="sm"
            disabled={readOnly || !strategyBody.trim() || busy !== null}
            onClick={() =>
              void mutate(
                "strategy-save",
                () =>
                  createStrategyRevision(slug, {
                    body: strategyBody.trim(),
                    summary: strategySummary.trim() || null,
                    state: "current",
                    based_on_revision_id: data.current?.id ?? null,
                  }),
                "Current strategy saved as a new revision.",
              )
            }
          >
            {busy === "strategy-save" ? "Saving…" : "Save new revision"}
          </Button>
        </div>
        <details className="mt-4 rounded border border-border bg-background/50 p-3">
          <summary className="cursor-pointer text-sm font-medium">Revision history ({data.revisions.length})</summary>
          <ul className="mt-3 space-y-2">
            {data.revisions.map((revision) => (
              <li key={revision.id} className="rounded border border-border p-3 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <span className="font-medium">v{revision.version}</span>{" "}
                    <Badge variant="outline" className="ml-1 text-[10px]">{revision.state}</Badge>
                    <span className="ml-2 text-muted-foreground">{revision.summary ?? "No summary"}</span>
                  </div>
                  <DateTime value={revision.created_at} />
                </div>
                <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-muted-foreground">{revision.body}</p>
                {!readOnly && revision.state === "proposed" && (
                  <p className="mt-2 text-right text-[10px] text-muted-foreground">Review this proposal in Needs decision.</p>
                )}
                {!readOnly && revision.state === "superseded" && (
                  <div className="mt-2 text-right">
                    <SmallAction onClick={() => void mutate(`revision-${revision.id}-restore`, () => decideStrategyRevision(slug, revision.id, "restore", { based_on_revision_id: data.current?.id ?? null }), `Revision v${revision.version} restored as current.`)}>Restore</SmallAction>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </details>
      </section>
        </TabsContent>
        <TabsContent value="objectives" className="space-y-6">
      <section className="rounded-lg border border-border bg-card/40 p-4">
        <h3 className="text-sm font-semibold">Objectives</h3>
        {!readOnly && (
          <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_9rem_auto]">
            <Input value={objectiveTitle} onChange={(event) => setObjectiveTitle(event.target.value)} placeholder="Add an objective" />
            <select value={objectivePriority} onChange={(event) => setObjectivePriority(event.target.value as ObjectivePriority)} className="rounded-md border border-input bg-background px-2 text-sm">
              {PRIORITIES.map((priority) => <option key={priority}>{priority}</option>)}
            </select>
            <Button size="sm" disabled={!objectiveTitle.trim() || busy !== null} onClick={() => void (async () => { if (await mutate("objective-create", () => createObjective(slug, { title: objectiveTitle.trim(), priority: objectivePriority }), "Objective created.")) setObjectiveTitle(""); })()}>Add</Button>
          </div>
        )}
        {data.objectives.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">No objectives yet.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {data.objectives.map((objective) => (
              <li key={objective.id} className="rounded border border-border bg-background p-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium">{objective.title}</p>
                    {objective.success_criteria && <p className="mt-1 text-xs text-muted-foreground">Success: {objective.success_criteria}</p>}
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">{objective.priority}</Badge>
                    <Badge variant="secondary">{objective.status}</Badge>
                  </div>
                </div>
                {!readOnly && (
                  <div className="mt-2 flex flex-wrap justify-end gap-2">
                    <select
                      value={objective.priority}
                      aria-label={`Priority for ${objective.title}`}
                      onChange={(event) => void mutate(`objective-${objective.id}-priority`, () => updateObjective(slug, objective.id, { expected_row_version: objective.row_version, priority: event.target.value as ObjectivePriority }), "Objective priority updated.")}
                      className="h-8 rounded border border-input bg-background px-2 text-xs"
                    >
                      {PRIORITIES.map((priority) => <option key={priority}>{priority}</option>)}
                    </select>
                    {objective.status === "completed" ? (
                      <SmallAction onClick={() => void mutate(`objective-${objective.id}-reopen`, () => transitionObjective(slug, objective.id, "reopen", objective.row_version), "Objective reopened.")}>Reopen</SmallAction>
                    ) : objective.status === "cancelled" ? null : (
                      <>
                        <SmallAction onClick={() => void mutate(`objective-${objective.id}-complete`, () => transitionObjective(slug, objective.id, "complete", objective.row_version), "Objective completed.")}>Complete</SmallAction>
                        <SmallAction onClick={() => void mutate(`objective-${objective.id}-cancel`, () => cancelObjective(slug, objective.id, objective.row_version, window.prompt("Cancellation reason") ?? undefined), "Objective cancelled.")}>Cancel</SmallAction>
                      </>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
        </TabsContent>
        <TabsContent value="work" className="space-y-6">
      <section className="rounded-lg border border-border bg-card/40 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">Work queue</h3>
            <p className="text-xs text-muted-foreground">Committed analyst and agent-assisted coordination work. Tactical Tasks remain in Status.</p>
          </div>
          <div className="flex gap-2">
            <Input value={workQuery} onChange={(event) => setWorkQuery(event.target.value)} placeholder="Filter work" className="h-8 w-40 text-xs" />
            <select value={workStatusFilter} onChange={(event) => setWorkStatusFilter(event.target.value as WorkItemStatus | "all")} className="h-8 rounded border border-input bg-background px-2 text-xs">
              {WORK_STATUSES.map((status) => <option key={status}>{status}</option>)}
            </select>
          </div>
        </div>
        {!readOnly && (
          <div className="mt-3 grid gap-2 lg:grid-cols-[1fr_8rem_11rem_12rem_auto]">
            <Input value={workTitle} onChange={(event) => setWorkTitle(event.target.value)} placeholder="Add committed work" />
            <select value={workPriority} onChange={(event) => setWorkPriority(event.target.value as WorkItemPriority)} className="rounded border border-input bg-background px-2 text-sm">{PRIORITIES.map((priority) => <option key={priority}>{priority}</option>)}</select>
            <select value={workExecutor} onChange={(event) => setWorkExecutor(event.target.value as WorkItemExecutor)} className="rounded border border-input bg-background px-2 text-sm">{EXECUTORS.map((executor) => <option key={executor}>{executor}</option>)}</select>
            <select value={workObjective} onChange={(event) => setWorkObjective(event.target.value)} className="rounded border border-input bg-background px-2 text-sm"><option value="">No objective</option>{data.objectives.map((objective) => <option key={objective.id} value={objective.id}>{objective.title}</option>)}</select>
            <Button size="sm" disabled={!workTitle.trim() || busy !== null} onClick={() => void (async () => { if (await mutate("work-create", () => createWorkItem(slug, { title: workTitle.trim(), priority: workPriority, executor_type: workExecutor, objective_id: workObjective || null }), "Work item created.")) setWorkTitle(""); })()}>Add</Button>
          </div>
        )}
        <ul className="mt-4 max-h-[40rem] space-y-2 overflow-y-auto pr-1">
            {visibleWork.map((item) => (
              <li key={item.id}>
                <button type="button" onClick={() => { setSelectedWorkId(item.id); setWorkFlyoutOpen(true); }} className={cn("w-full rounded border p-3 text-left hover:bg-muted/40", selectedWorkId === item.id ? "border-foreground/40 bg-muted/30" : "border-border bg-background")}>
                  <div className="flex items-start justify-between gap-2"><span className="font-medium">{item.title}</span><span className="flex gap-1"><Badge variant="outline">{item.priority}</Badge><Badge variant="secondary">{item.status}</Badge></span></div>
                  <p className="mt-1 text-xs text-muted-foreground">{item.executor_type}{item.due_at ? <> · due <DateTime value={item.due_at} /></> : null}</p>
                  {item.blocked_reason && <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">Blocked: {item.blocked_reason}</p>}
                </button>
              </li>
            ))}
            {visibleWork.length === 0 && <li className="text-sm text-muted-foreground">No matching work.</li>}
          </ul>
        <WorkItemFlyout item={selectedWork} objectives={data.objectives} scope={data.scope} tools={data.tools} scopeError={sliceErrors.scope ?? null} toolsError={sliceErrors.tools ?? null} slug={slug} readOnly={readOnly} busy={busy} mutate={mutate} open={workFlyoutOpen} onOpenChange={handleWorkFlyoutOpenChange} onBackToStrategy={() => { setTab("strategy"); handleWorkFlyoutOpenChange(false); }} />
      </section>
        </TabsContent>
        <TabsContent value="coverage" className="space-y-6">
      <details className="rounded-lg border border-border bg-card/40 p-4">
        <summary className="cursor-pointer text-sm font-semibold">Checkpoints and activity snapshots</summary>
        {!readOnly && <div className="mt-3 flex gap-2"><Input value={checkpointNarrative} onChange={(event) => setCheckpointNarrative(event.target.value)} placeholder="Optional end-of-session note" /><Button size="sm" onClick={() => void (async () => { if (await mutate("checkpoint-create", () => createCheckpoint(slug, checkpointNarrative.trim() || undefined), "Checkpoint created.")) setCheckpointNarrative(""); })()}>Create</Button></div>}
        <ul className="mt-3 max-h-64 space-y-2 overflow-y-auto">
          {data.checkpoints.map((checkpoint) => <li key={checkpoint.id} className="rounded border border-border bg-background p-3 text-xs"><div className="flex justify-between"><span className="font-medium">Checkpoint</span><DateTime value={checkpoint.created_at} /></div><p className="mt-1 text-muted-foreground">{checkpoint.narrative ?? "Deterministic state snapshot"}</p></li>)}
          {data.checkpoints.length === 0 && <li className="text-sm text-muted-foreground">No checkpoints yet.</li>}
        </ul>
      </details>

      <details className="rounded-lg border border-border bg-card/40 p-4">
        <summary className="cursor-pointer text-sm font-semibold">Coverage items</summary>
        {!readOnly && <div className="mt-3 grid gap-2 sm:grid-cols-[8rem_1fr_13rem_auto]"><Input value={coverageKind} onChange={(event) => setCoverageKind(event.target.value)} placeholder="Target kind" /><Input value={coverageTarget} onChange={(event) => setCoverageTarget(event.target.value)} placeholder="Target key" /><select value={coverageCategory} onChange={(event) => setCoverageCategory(event.target.value)} className="rounded border border-input bg-background px-2 text-sm">{COVERAGE_CATEGORIES.map((category) => <option key={category}>{category}</option>)}</select><Button size="sm" disabled={!coverageTarget.trim()} onClick={() => void (async () => { if (await mutate("coverage-create", () => createCoverageItem(slug, { target_kind: coverageKind.trim(), target_key: coverageTarget.trim(), activity_category: coverageCategory }), "Coverage item created.")) setCoverageTarget(""); })()}>Add</Button></div>}
        <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr className="border-b border-border text-muted-foreground"><th className="py-2">Target</th><th>Category</th><th>Status</th><th>Reason</th></tr></thead><tbody>{data.coverage.map((item) => <tr key={item.id} className="border-b border-border/60"><td className="py-2 font-mono">{item.target_kind}:{item.target_key}</td><td>{item.activity_category}</td><td>{readOnly ? <Badge variant="outline">{item.status}</Badge> : <select value={item.status} onChange={(event) => { const next = event.target.value as CoverageStatus; const reason = next === "accepted_gap" ? window.prompt("Accepted gap rationale (required)") ?? "" : item.reason ?? ""; if (next === "accepted_gap" && !reason.trim()) return; void mutate(`coverage-${item.id}`, () => updateCoverageItem(slug, item, next, reason), "Coverage updated."); }} className="h-8 rounded border border-input bg-background px-2">{COVERAGE_STATUSES.map((status) => <option key={status}>{status}</option>)}</select>}</td><td className="max-w-xs truncate">{item.reason ?? "—"}</td></tr>)}</tbody></table>{data.coverage.length === 0 && <p className="py-3 text-sm text-muted-foreground">No coverage rows yet.</p>}</div>
      </details>

      <details className="rounded-lg border border-border bg-card/40 p-4">
        <summary className="cursor-pointer text-sm font-semibold">Completion readiness</summary>
        <div className="mt-4">
          <CompletionSection slug={slug} engagementStatus={engagementStatus} readiness={data.completion} decisions={data.decisions} exceptions={completionExceptions} setExceptions={setCompletionExceptions} busy={busy} mutate={mutate} />
        </div>
      </details>
        </TabsContent>
        <TabsContent value="strategist" className="space-y-6">
          <StrategistSection slug={slug} readOnly={readOnly} chat={data.chat} message={strategistMessage} setMessage={setStrategistMessage} lastRun={lastStrategistRun} setLastRun={setLastStrategistRun} busy={busy} mutate={mutate} hasCurrentStrategy />
        </TabsContent>
      </Tabs>

      <Dialog open={strategyFlyoutOpen} onOpenChange={setStrategyFlyoutOpen}>
        <DialogContent className="max-w-5xl max-h-[88vh] w-[95vw] gap-0 overflow-hidden p-0">
          <div className="flex items-center justify-between gap-3 border-b border-border p-4">
            <div>
              <DialogTitle className="text-base">Strategy {data.current ? `v${data.current.version}` : ""}</DialogTitle>
              <DialogDescription className="text-xs">
                {strategyFlyoutEditing ? "Edit the markdown body — Save writes a new revision." : "Rendered view of the current strategy body."}
              </DialogDescription>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex overflow-hidden rounded-md border border-border">
                <button type="button" disabled={readOnly} onClick={() => setStrategyFlyoutEditing(false)} className={cn("px-3 py-1 text-xs", !strategyFlyoutEditing ? "bg-muted text-foreground" : "text-muted-foreground")}>View</button>
                <button type="button" disabled={readOnly} onClick={() => setStrategyFlyoutEditing(true)} className={cn("border-l border-border px-3 py-1 text-xs", strategyFlyoutEditing ? "bg-muted text-foreground" : "text-muted-foreground")}>Edit</button>
              </div>
              <DialogClose asChild>
                <Button size="sm" variant="ghost">Close</Button>
              </DialogClose>
            </div>
          </div>
          <div className="max-h-[78vh] overflow-y-auto p-4">
            {strategyFlyoutEditing ? (
              <div className="space-y-3">
                <Input value={strategySummary} onChange={(event) => setStrategySummary(event.target.value)} placeholder="Revision summary" disabled={readOnly} />
                <Textarea value={strategyBody} onChange={(event) => setStrategyBody(event.target.value)} rows={26} placeholder="Mission, hypotheses, priorities, constraints, coverage expectations, and exit criteria…" disabled={readOnly} className="font-mono text-xs" />
                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => setStrategyFlyoutEditing(false)}>Done</Button>
                  <Button size="sm" disabled={readOnly || !strategyBody.trim() || busy !== null} onClick={() => void mutate("strategy-save", () => createStrategyRevision(slug, { body: strategyBody.trim(), summary: strategySummary.trim() || null, state: "current", based_on_revision_id: data.current?.id ?? null }), "Current strategy saved as a new revision.").then((ok) => { if (ok) setStrategyFlyoutEditing(false); })}>{busy === "strategy-save" ? "Saving…" : "Save new revision"}</Button>
                </div>
              </div>
            ) : (
              <StrategyMarkdown body={strategyBody} />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function InitialStrategyBuilder({ slug, readOnly, summary, setSummary, sections, setSections, busy, mutate }: { slug: string; readOnly: boolean; summary: string; setSummary: (value: string) => void; sections: Record<string, string>; setSections: (value: Record<string, string>) => void; busy: string | null; mutate: (key: string, action: () => Promise<unknown>, success: string) => Promise<boolean> }) {
  const body = STRATEGY_SECTIONS.map((section) => `## ${section}\n${sections[section]?.trim() ?? ""}`).join("\n\n").trim();
  const hasBody = STRATEGY_SECTIONS.some((section) => sections[section]?.trim());
  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Create strategy manually</h3>
          <p className="text-xs text-muted-foreground">Fill the same sections the AI generates. Saving makes this the current strategy.</p>
        </div>
        <Button size="sm" disabled={readOnly || !hasBody || busy !== null} onClick={() => void mutate("strategy-manual-create", () => createStrategyRevision(slug, { body, summary: summary.trim() || "Initial strategy", state: "current", based_on_revision_id: null }), "Initial strategy created.")}>{busy === "strategy-manual-create" ? "Saving…" : "Save strategy"}</Button>
      </div>
      <Input value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="Strategy summary" disabled={readOnly} className="mt-3" />
      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        {STRATEGY_SECTIONS.map((section) => (
          <label key={section} className="block rounded border border-border bg-background/60 p-3 text-xs">
            <span className="font-medium">{section}</span>
            <Textarea value={sections[section] ?? ""} onChange={(event) => setSections({ ...sections, [section]: event.target.value })} placeholder={`Draft ${section.toLowerCase()}…`} rows={5} disabled={readOnly} className="mt-2" />
          </label>
        ))}
      </div>
    </section>
  );
}

function StrategyHeader({ busy, refresh }: { busy: string | null; refresh: () => Promise<void> }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-xl font-semibold">Strategy</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Engagement Strategist, durable decisions, current focus, and completion readiness.
        </p>
      </div>
      <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={busy !== null}>Refresh</Button>
    </header>
  );
}

function ReadOnlyNotice({ engagementStatus }: { engagementStatus: EngagementStatus }) {
  return (
    <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-200">
      {engagementStatus !== "active"
        ? `This engagement is ${engagementStatus}. Strategy and work are read-only.`
        : "This engagement is completed. Reopen it before changing strategy or work."}
    </div>
  );
}

function StrategyRequiredGate({ findingCount }: { findingCount: number }) {
  return (
    <section className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4">
      <h3 className="text-sm font-semibold">Initial strategy required</h3>
      <p className="mt-1 text-sm text-muted-foreground">
        This engagement has no accepted current strategy{findingCount ? ` and ${findingCount} finding${findingCount === 1 ? "" : "s"}` : ""}. Generate an initial strategy with the Engagement Strategist, then accept the durable proposal in Needs decision before work, coverage, checkpoint, or completion sections are populated.
      </p>
    </section>
  );
}

// ── Needs-decision outcome tags ──
type DecisionTag = "Agent run" | "Work item" | "Strategy" | "Signal";

function suggestionTag(suggestion: Suggestion): DecisionTag {
  if (suggestion.kind === "strategy_revision") return "Strategy";
  if (suggestion.kind === "task") {
    const taskKind = suggestion.payload?.task_kind as string | undefined;
    const owner = suggestion.payload?.owner_eligibility as string | undefined;
    // Only tag "Agent run" when BOTH conditions are met; a missing
    // owner_eligibility must NOT default to "Agent run" — fall through to
    // "Work item" so the analyst isn't misled about what Accept does.
    if (
      (taskKind === "scan" || taskKind === "enum") &&
      (owner === "agent" || owner === "either")
    ) {
      return "Agent run";
    }
  }
  return "Work item";
}

const TAG_STYLES: Record<DecisionTag, string> = {
  "Agent run": "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  "Work item": "border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-300",
  "Strategy": "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  "Signal": "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
};

function TagBadge({ tag }: { tag: DecisionTag }) {
  return (
    <span className={cn("rounded-full border px-2 py-0.5 text-[10px] font-medium whitespace-nowrap", TAG_STYLES[tag])}>
      {tag}
    </span>
  );
}

interface DecisionItem {
  key: string;
  tag: DecisionTag;
  title: string;
  description: string | null;
  findingId: string | null;
  accept: () => Promise<unknown>;
  dismiss: () => Promise<unknown>;
}

function NeedsDecisionSection({ slug, readOnly, openSignals, openSuggestions, proposedRevisions, currentRevisionId, busy, mutate }: { slug: string; readOnly: boolean; openSignals: StrategySignal[]; openSuggestions: Suggestion[]; proposedRevisions: StrategyRevision[]; currentRevisionId: string | null; busy: string | null; mutate: (key: string, action: () => Promise<unknown>, success: string) => Promise<boolean> }) {
  const [collapsed, setCollapsed] = useState(false);
  const [activeTag, setActiveTag] = useState<DecisionTag | "All">("All");
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());

  // Build the unified items list.
  const items: DecisionItem[] = useMemo(() => [
    ...proposedRevisions.map<DecisionItem>((rev) => ({
      key: `revision:${rev.id}`,
      tag: "Strategy" as const,
      title: `Strategy revision v${rev.version}`,
      description: rev.summary ?? "Proposed strategy revision",
      findingId: null,
      accept: () => decideStrategyRevision(slug, rev.id, "accept", { based_on_revision_id: currentRevisionId }),
      dismiss: () => decideStrategyRevision(slug, rev.id, "reject", { based_on_revision_id: currentRevisionId }),
    })),
    ...openSuggestions.map<DecisionItem>((s) => ({
      key: `suggestion:${s.id}`,
      tag: suggestionTag(s),
      title: s.title,
      description: s.body ?? labelSuggestionKind(s.kind),
      findingId: s.finding_id,
      accept: () => acceptSuggestion(s.id),
      dismiss: () => dismissSuggestion(s.id),
    })),
    ...openSignals.map<DecisionItem>((sig) => ({
      key: `signal:${sig.id}`,
      tag: "Signal" as const,
      title: sig.signal_type,
      description: sig.summary,
      findingId: null,
      accept: () => decideStrategySignal(sig.id, "incorporate"),
      dismiss: () => decideStrategySignal(sig.id, "dismiss"),
    })),
  ], [proposedRevisions, openSuggestions, openSignals, slug, currentRevisionId]);

  const total = items.length;
  const tagsPresent = useMemo(() => {
    const set = new Set<DecisionTag>();
    items.forEach((i) => set.add(i.tag));
    return ["All", ...Array.from(set)] as const;
  }, [items]);

  const visibleItems = activeTag === "All" ? items : items.filter((i) => i.tag === activeTag);
  const selectedItems = items.filter((i) => selectedKeys.has(i.key));

  const toggleSelected = (key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const bulkAction = async (actionItems: DecisionItem[], action: "accept" | "dismiss") => {
    if (actionItems.length === 0) return;
    const agentRunCount = actionItems.filter((item) => item.tag === "Agent run").length;
    if (
      action === "accept" &&
      agentRunCount > 0 &&
      !window.confirm(
        `Accept ${agentRunCount} Agent run proposal${agentRunCount === 1 ? "" : "s"}? Acceptance immediately requests scope-gated Tactical dispatch. Active tools may still pause for approval.`,
      )
    ) return;

    await mutate(
      "decisions-bulk",
      async () => {
        const results = await Promise.allSettled(
          actionItems.map((item) => action === "accept" ? item.accept() : item.dismiss()),
        );
        const failedKeys = new Set(
          results.flatMap((result, index) => result.status === "rejected" ? [actionItems[index].key] : []),
        );
        setSelectedKeys((previous) => {
          const next = new Set(previous);
          actionItems.forEach((item) => next.delete(item.key));
          failedKeys.forEach((key) => next.add(key));
          return next;
        });
        if (failedKeys.size > 0) {
          const succeeded = actionItems.length - failedKeys.size;
          throw new Error(
            `${succeeded} decision${succeeded === 1 ? "" : "s"} updated; ${failedKeys.size} failed. Failed decisions remain selected so you can retry.`,
          );
        }
      },
      `${action === "accept" ? "Accepted" : "Dismissed"} ${actionItems.length} decision${actionItems.length === 1 ? "" : "s"}.`,
    );
  };

  const busyBulk = busy === "decisions-bulk";

  return (
    <section className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4">
      {/* Header: collapse toggle + count */}
      <div className="flex items-center justify-between gap-3">
        <button type="button" onClick={() => setCollapsed((v) => !v)} className="flex items-center gap-2 text-left">
          <span className="text-xs text-muted-foreground">{collapsed ? "▶" : "▼"}</span>
          <h3 className="text-sm font-semibold">Needs decision ({total})</h3>
        </button>
        <Badge variant={total ? "secondary" : "outline"}>{total} open</Badge>
      </div>

      {!collapsed && (
        <>
          {/* Tag filter chips */}
          {tagsPresent.length > 2 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {tagsPresent.map((tag) => (
                <button
                  key={tag}
                  type="button"
                  onClick={() => setActiveTag(tag)}
                  className={cn(
                    "rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors",
                    activeTag === tag
                      ? "border-foreground/40 bg-muted text-foreground"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  {tag}
                  {tag !== "All" && (
                    <span className="ml-1 opacity-60">
                      ({items.filter((i) => i.tag === tag).length})
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}

          {/* Bulk action bar */}
          {!readOnly && visibleItems.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-b border-border/60 pb-3">
              <SmallAction onClick={() => void bulkAction(visibleItems, "accept")} disabled={busy !== null}>
                {busyBulk ? "Working…" : `Accept all${activeTag !== "All" ? ` (${activeTag})` : ""}`}
              </SmallAction>
              <SmallAction onClick={() => void bulkAction(visibleItems, "dismiss")} disabled={busy !== null}>
                {busyBulk ? "…" : `Dismiss all${activeTag !== "All" ? ` (${activeTag})` : ""}`}
              </SmallAction>
              {selectedItems.length > 0 && (
                <>
                  <span className="text-[10px] text-muted-foreground">|</span>
                  <SmallAction onClick={() => void bulkAction(selectedItems, "accept")} disabled={busy !== null}>
                    Accept selected ({selectedItems.length})
                  </SmallAction>
                  <SmallAction onClick={() => void bulkAction(selectedItems, "dismiss")} disabled={busy !== null}>
                    Dismiss selected ({selectedItems.length})
                  </SmallAction>
                </>
              )}
            </div>
          )}

          {/* Items list */}
          <ul className="mt-3 space-y-2">
            {visibleItems.map((item) => (
              <li key={item.key} className="rounded border border-border bg-background p-3 text-xs">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex items-start gap-2">
                    {!readOnly && (
                      <input
                        type="checkbox"
                        checked={selectedKeys.has(item.key)}
                        onChange={() => toggleSelected(item.key)}
                        className="mt-0.5 h-3.5 w-3.5 rounded border-border"
                      />
                    )}
                    <div>
                      <div className="flex items-center gap-2">
                        <TagBadge tag={item.tag} />
                        <p className="font-medium">{item.title}</p>
                      </div>
                      {item.description && <p className="mt-1 text-muted-foreground">{item.description}</p>}
                      {item.tag === "Agent run" && (
                        <p className="mt-1 text-[10px] text-sky-700 dark:text-sky-300">
                          Acceptance immediately requests a scope-gated Tactical dispatch. Active tools may still pause for approval.
                        </p>
                      )}
                      {item.findingId && (
                        <Link
                          href={`/e/findings/${item.findingId}?slug=${encodeURIComponent(slug)}&returnTo=${encodeURIComponent(`/e?slug=${slug}&view=strategy`)}`}
                          className="mt-2 inline-block text-[10px] hover:underline"
                        >
                          Open source finding →
                        </Link>
                      )}
                    </div>
                  </div>
                </div>
                {!readOnly && (
                  <div className="mt-2 flex justify-end gap-2">
                    <SmallAction
                      onClick={() => void mutate(`${item.key}-dismiss`, item.dismiss, `Dismissed: ${item.title}`)}
                      disabled={busy !== null}
                    >
                      Dismiss
                    </SmallAction>
                    <SmallAction
                      onClick={() => {
                        if (
                          item.tag === "Agent run" &&
                          !window.confirm(
                            "Accept this Agent run proposal? Acceptance immediately requests scope-gated Tactical dispatch. Active tools may still pause for approval.",
                          )
                        ) {
                          return;
                        }
                        void mutate(
                          `${item.key}-accept`,
                          item.accept,
                          item.tag === "Agent run"
                            ? `Run requested: ${item.title}`
                            : `Accepted: ${item.title}`,
                        );
                      }}
                      disabled={busy !== null}
                    >
                      {item.tag === "Agent run" ? "Accept & request run" : "Accept"}
                    </SmallAction>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function ResumeSection({ resume, slug }: { resume: ResumeBriefing; slug: string }) {
  const strategyRequired = Boolean(resume.current_focus.strategy_required);
  return (
    <section className="rounded-lg border border-sky-500/30 bg-sky-500/5 p-4">
      <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold">Resume engagement</h3><p className="text-xs text-muted-foreground">Deterministic briefing · <DateTime value={resume.generated_at} /></p></div><Link href={`/e?slug=${encodeURIComponent(slug)}&view=status`} className="text-xs hover:underline">Open execution Status →</Link></div>
      {strategyRequired ? (
        <p className="mt-3 rounded border border-dashed border-sky-500/30 p-3 text-sm text-muted-foreground">Resume briefing is intentionally limited until an initial strategy is accepted.</p>
      ) : (
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <ResumeCard title="Recommended work" items={resume.current_tasks} empty="No recommended work focus." />
          <ResumeCard title="Recent findings" items={resume.recent_findings} empty="No recent finding activity." />
          <ResumeCard title="Recently closed work" items={resume.recently_closed} empty="No recently closed work." />
          <ResumeCard title="Recent activity" items={resume.recent_activity} empty="No activity since checkpoint." />
        </div>
      )}
    </section>
  );
}

function ResumeCard({ title, items, empty }: { title: string; items?: ResumeRecordRef[]; empty: string }) {
  const safeItems = items ?? [];
  return (
    <div className="rounded border border-sky-500/20 bg-background/60 p-3">
      <p className="text-xs font-medium">{title}</p>
      {safeItems.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">{empty}</p>
      ) : (
        <ul className="mt-2 space-y-2 text-xs">
          {safeItems.slice(0, 5).map((item) => (
            <li key={`${item.type}-${item.id}`}>
              <ResumeItem item={item} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ResumeItem({ item }: { item: ResumeRecordRef }) {
  const label = item.action
    ? `${item.action}${item.finding?.title ? ` - ${item.finding.title}` : ""}${item.actor ? ` - ${item.actor}` : ""}`
    : item.title ?? item.summary ?? item.id;
  const body = (
    <>
      <span className="font-medium text-foreground">{label}</span>
      <span className="ml-2 text-muted-foreground">
        {[item.severity, item.priority, item.status].filter(Boolean).join(" · ")}
      </span>
    </>
  );
  return item.href ? <Link href={item.href} className="hover:underline">{body}</Link> : <span>{body}</span>;
}

function labelSuggestionKind(kind: Suggestion["kind"]): string {
  return kind.replaceAll("_", " ");
}

const PREFERRED_TOOLS: Record<string, string[]> = {
  domain: ["subfinder"],
  ip: ["portscan", "port_scan"],
  cidr: ["subnet_sweep"],
  url: ["httpx_probe"],
};

function executionIdempotencyKey(
  workItemId: string,
  rowVersion: number,
  tool: string,
  target: string,
): string {
  const value = `${tool}\u0000${target}`;
  let first = 0xdeadbeef ^ value.length;
  let second = 0x41c6ce57 ^ value.length;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 2654435761);
    second = Math.imul(second ^ code, 1597334677);
  }
  first = Math.imul(first ^ (first >>> 16), 2246822507) ^ Math.imul(second ^ (second >>> 13), 3266489909);
  second = Math.imul(second ^ (second >>> 16), 2246822507) ^ Math.imul(first ^ (first >>> 13), 3266489909);
  const digest = `${(second >>> 0).toString(36)}${(first >>> 0).toString(36)}`;
  return `work-item-execution:${workItemId}:${rowVersion}:${digest}`;
}

function SafeRunProposal({
  item,
  scope,
  tools,
  scopeError,
  toolsError,
  readOnly,
  busy,
  mutate,
}: {
  item: WorkItem;
  scope: ScopeItem[];
  tools: OrchestratorTool[];
  scopeError: string | null;
  toolsError: string | null;
  readOnly: boolean;
  busy: string | null;
  mutate: (key: string, action: () => Promise<unknown>, success: string) => Promise<boolean>;
}) {
  const workItemId = item.id;
  const scopeItemId = item.scope_item_id;
  const anchor = scope.find((candidate) => candidate.id === scopeItemId) ?? null;
  const compatibleTools = useMemo(() => {
    if (!anchor || anchor.is_exclusion) return [];
    const candidates = tools.filter(
      (tool) =>
        tool.scope_kind === anchor.kind &&
        tool.risk !== "destructive" &&
        tool.phase !== "exploit" &&
        tool.phase !== "phishing",
    );
    const preferences = PREFERRED_TOOLS[anchor.kind] ?? [];
    return [...candidates].sort((left, right) => {
      const leftRank = preferences.indexOf(left.name);
      const rightRank = preferences.indexOf(right.name);
      if (leftRank >= 0 || rightRank >= 0) {
        return (leftRank < 0 ? Number.MAX_SAFE_INTEGER : leftRank) -
          (rightRank < 0 ? Number.MAX_SAFE_INTEGER : rightRank);
      }
      return left.name.localeCompare(right.name);
    });
  }, [anchor, tools]);
  const toolNames = compatibleTools.map((tool) => tool.name).join("\u0000");
  const defaultToolName = compatibleTools[0]?.name ?? "";
  const [toolName, setToolName] = useState("");
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    setToolName(defaultToolName);
    setFeedback(null);
  }, [workItemId, scopeItemId, anchor?.kind, anchor?.value, defaultToolName, toolNames]);

  const selectedTool = compatibleTools.find((tool) => tool.name === toolName) ?? null;
  const taskKind = selectedTool?.risk === "passive" || selectedTool?.phase === "osint"
    ? "enum"
    : "scan";
  const proposing = busy === `work-${item.id}-execution-suggestion`;

  if (!item.scope_item_id) {
    return (
      <div className="rounded border border-amber-500/30 bg-amber-500/5 p-3">
        <p className="text-xs font-medium">Propose safe run</p>
        <p className="mt-1 text-xs text-muted-foreground">
          This work item has no formal scope anchor. {item.entity_id ? "Its entity anchor alone cannot prefill a scope-checked target." : "Add a scope anchor before proposing a run."}
        </p>
      </div>
    );
  }
  if (!anchor) {
    return (
      <div className="rounded border border-amber-500/30 bg-amber-500/5 p-3">
        <p className="text-xs font-medium">Propose safe run</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {scopeError ? `Formal scope could not be loaded: ${scopeError}` : "The work item's scope anchor is no longer present in formal scope."}
        </p>
      </div>
    );
  }
  if (anchor.is_exclusion) {
    return (
      <div className="rounded border border-amber-500/30 bg-amber-500/5 p-3">
        <p className="text-xs font-medium">Propose safe run</p>
        <p className="mt-1 text-xs text-muted-foreground">The anchored scope item is an exclusion, so no run can be proposed from it.</p>
      </div>
    );
  }
  if (compatibleTools.length === 0) {
    return (
      <div className="rounded border border-amber-500/30 bg-amber-500/5 p-3">
        <p className="text-xs font-medium">Propose safe run</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {toolsError ? `The execution tool catalog could not be loaded: ${toolsError}` : `No safe catalog tool accepts ${anchor.kind} targets.`}
        </p>
      </div>
    );
  }

  const propose = async () => {
    if (!selectedTool) return;
    setFeedback(null);
    const ok = await mutate(
      `work-${item.id}-execution-suggestion`,
      () =>
        createExecutionSuggestion(item.id, {
          tool: selectedTool.name,
          target: anchor.value,
          task_kind: taskKind,
          title: `Proposed ${selectedTool.name} run: ${item.title}`.slice(0, 300),
          expected_work_item_version: item.row_version,
          idempotency_key: executionIdempotencyKey(
            item.id,
            item.row_version,
            selectedTool.name,
            anchor.value,
          ),
          finding_id: item.finding_links[0]?.finding_id ?? null,
        }),
      "Run proposal created. Nothing executed; an analyst must accept it in Needs decision.",
    );
    setFeedback(ok
      ? { kind: "success", text: "Proposal created. It will appear in Needs decision as the refresh completes. Nothing executed; an analyst must accept the run proposal before the existing approval and Tactical gates apply." }
      : { kind: "error", text: "The proposal was not created. The backend rejected the request; review the page error and try again." });
  };

  return (
    <div className="rounded border border-sky-500/30 bg-sky-500/5 p-3">
      <p className="text-xs font-medium">Propose safe run</p>
      <p className="mt-1 text-[11px] text-muted-foreground">
        This creates an inert proposal only. It does not execute or dispatch a tool; an analyst must accept the new run proposal in Needs decision.
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-[11rem_1fr_auto]">
        <select
          aria-label="Compatible execution tool"
          value={toolName}
          onChange={(event) => { setToolName(event.target.value); setFeedback(null); }}
          disabled={readOnly || proposing}
          className="h-8 rounded border border-input bg-background px-2 text-xs"
        >
          {compatibleTools.map((tool) => <option key={tool.name} value={tool.name}>{tool.name}</option>)}
        </select>
        <Input
          aria-label="Scope-anchored execution target"
          value={anchor.value}
          readOnly
          className="h-8 font-mono text-xs"
        />
        <Button size="sm" variant="outline" disabled={readOnly || busy !== null} onClick={() => void propose()}>
          {proposing ? "Proposing…" : "Propose safe run"}
        </Button>
      </div>
      <p className="mt-2 text-[10px] text-muted-foreground">
        Anchored to {anchor.kind}: <span className="font-mono">{anchor.value}</span> · task kind {taskKind}
        {readOnly ? " · this engagement is read-only" : ""}
      </p>
      {feedback && (
        <p role={feedback.kind === "error" ? "alert" : "status"} className={cn("mt-2 text-xs", feedback.kind === "error" ? "text-critical" : "text-emerald-700 dark:text-emerald-200")}>
          {feedback.text}
        </p>
      )}
    </div>
  );
}

function WorkItemFlyout({ item, objectives, scope, tools, scopeError, toolsError, slug, readOnly, busy, mutate, open, onOpenChange, onBackToStrategy }: { item: WorkItem | null; objectives: Objective[]; scope: ScopeItem[]; tools: OrchestratorTool[]; scopeError: string | null; toolsError: string | null; slug: string; readOnly: boolean; busy: string | null; mutate: (key: string, action: () => Promise<unknown>, success: string) => Promise<boolean>; open: boolean; onOpenChange: (value: boolean) => void; onBackToStrategy: () => void; }) {
  const [title, setTitle] = useState(item?.title ?? "");
  const [description, setDescription] = useState(item?.description ?? "");
  const [rationale, setRationale] = useState(item?.rationale ?? "");
  const [criteria, setCriteria] = useState((item?.acceptance_criteria ?? []).join("\n"));
  const [priority, setPriority] = useState<WorkItemPriority>(item?.priority ?? "medium");
  const [resolution, setResolution] = useState<WorkItemResolution>("completed");
  const itemId = item?.id;
  const itemRowVersion = item?.row_version;
  const itemTitle = item?.title ?? "";
  const itemDescription = item?.description ?? "";
  const itemRationale = item?.rationale ?? "";
  const itemCriteria = (item?.acceptance_criteria ?? []).join("\n");
  const itemPriority = item?.priority ?? "medium";

  // Re-sync the draft when the backing item changes (selection swap, or a
  // row-version bump after a successful save). Primitive dependencies avoid
  // clobbering an in-flight draft when polling recreates an unchanged object.
  useEffect(() => {
    if (!itemId) return;
    setTitle(itemTitle);
    setDescription(itemDescription);
    setRationale(itemRationale);
    setCriteria(itemCriteria);
    setPriority(itemPriority);
  }, [itemCriteria, itemDescription, itemId, itemPriority, itemRationale, itemRowVersion, itemTitle]);

  if (!item) return null;
  const objective = objectives.find((row) => row.id === item.objective_id);
  const terminal = item.status === "completed" || item.status === "cancelled";
  const dirty =
    title !== item.title ||
    (description || "") !== (item.description ?? "") ||
    (rationale || "") !== (item.rationale ?? "") ||
    criteria !== item.acceptance_criteria.join("\n") ||
    priority !== item.priority;

  const save = () =>
    mutate(
      "work-save",
      () =>
        updateWorkItem(item.id, {
          expected_row_version: item.row_version,
          title: title.trim(),
          description: description.trim() || null,
          rationale: rationale.trim() || null,
          acceptance_criteria: criteria.split("\n").map((line) => line.trim()).filter(Boolean),
          priority,
        }),
      "Work item saved.",
    ).then((ok) => {
      if (ok) onOpenChange(false);
    });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[88vh] w-[95vw] gap-0 overflow-hidden p-0">
        <div className="flex items-center justify-between gap-3 border-b border-border p-4">
          <DialogTitle className="flex flex-wrap items-center gap-2 text-base">
            <Badge variant="secondary">{item.status}</Badge>
            <Badge variant="outline">{item.priority}</Badge>
            <span className="text-xs font-normal text-muted-foreground">{item.executor_type}{objective ? ` · ${objective.title}` : ""}</span>
          </DialogTitle>
          <div className="flex items-center gap-2">
            <button type="button" onClick={onBackToStrategy} className="text-xs text-muted-foreground hover:underline">← Strategy / Resume</button>
            <DialogClose asChild>
              <Button size="sm" variant="ghost">Close</Button>
            </DialogClose>
          </div>
        </div>
        <div className="max-h-[78vh] space-y-4 overflow-y-auto p-4">
          <label className="block space-y-1">
            <span className="text-xs font-medium">Title</span>
            <Input value={title} onChange={(event) => setTitle(event.target.value)} disabled={readOnly} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-medium">Description</span>
            <Textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={5} disabled={readOnly} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-medium">Rationale</span>
            <Textarea value={rationale} onChange={(event) => setRationale(event.target.value)} rows={3} disabled={readOnly} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-medium">Acceptance criteria <span className="font-normal text-muted-foreground">(one per line)</span></span>
            <Textarea value={criteria} onChange={(event) => setCriteria(event.target.value)} rows={3} disabled={readOnly} />
          </label>
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium">Priority</span>
            <select value={priority} onChange={(event) => setPriority(event.target.value as WorkItemPriority)} disabled={readOnly} className="h-8 rounded border border-input bg-background px-2 text-xs">
              {PRIORITIES.map((value) => <option key={value}>{value}</option>)}
            </select>
          </div>

          {item.finding_links.length > 0 && (
            <div>
              <p className="text-xs font-medium">Linked findings</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {item.finding_links.map((link) => (
                  <Link key={`${link.finding_id}-${link.relationship}`} href={`/e/findings/${link.finding_id}?slug=${encodeURIComponent(slug)}&returnTo=${encodeURIComponent(`/e?slug=${slug}&view=strategy&workItem=${item.id}`)}`} className="rounded-full border border-border px-2 py-1 text-[10px] hover:underline">Finding · {link.relationship}</Link>
                ))}
              </div>
            </div>
          )}

          {(["finding_agent", "tactical"] as WorkItemExecutor[]).includes(item.executor_type) && !terminal && (
            <SafeRunProposal item={item} scope={scope} tools={tools} scopeError={scopeError} toolsError={toolsError} readOnly={readOnly} busy={busy} mutate={mutate} />
          )}

          {!readOnly && (
            <div className="space-y-3 border-t border-border pt-3">
              <div className="flex flex-wrap gap-2">
                {(item.status === "ready" || item.status === "blocked") && <SmallAction onClick={() => void mutate(`work-${item.id}-start`, () => transitionWorkItem(item.id, "start", item.row_version), "Work started.")}>Start</SmallAction>}
                {item.status === "in_progress" && <SmallAction onClick={() => { const reason = window.prompt("Blocking reason"); if (reason?.trim()) void mutate(`work-${item.id}-block`, () => blockWorkItem(item.id, item.row_version, reason.trim()), "Work blocked."); }}>Block</SmallAction>}
                {!terminal && item.status !== "deferred" && <SmallAction onClick={() => void mutate(`work-${item.id}-defer`, () => transitionWorkItem(item.id, "defer", item.row_version, window.prompt("Deferral reason") ?? undefined), "Work deferred.")}>Defer</SmallAction>}
                {(item.status === "deferred" || item.status === "completed") && <SmallAction onClick={() => void mutate(`work-${item.id}-reopen`, () => transitionWorkItem(item.id, "reopen", item.row_version), "Work reopened.")}>Reopen</SmallAction>}
                {!terminal && <SmallAction onClick={() => void mutate(`work-${item.id}-cancel`, () => transitionWorkItem(item.id, "cancel", item.row_version, window.prompt("Cancellation reason") ?? undefined), "Work cancelled.")}>Cancel</SmallAction>}
              </div>
              {!terminal && (
                <div className="flex flex-wrap items-center gap-2">
                  <select value={resolution} onChange={(event) => setResolution(event.target.value as WorkItemResolution)} className="h-8 rounded border border-input bg-background px-2 text-xs">
                    {RESOLUTIONS.map((outcome) => <option key={outcome}>{outcome}</option>)}
                  </select>
                  <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void mutate(`work-${item.id}-resolve`, () => resolveWorkItem(item.id, item.row_version, resolution, window.prompt("Resolution note") ?? undefined), "Work resolved.")}>Resolve</Button>
                </div>
              )}
              <div className="flex justify-end">
                <Button size="sm" disabled={!dirty || busy !== null} onClick={() => void save()}>{busy === "work-save" ? "Saving…" : "Save changes"}</Button>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function StrategistSection({ slug, readOnly, chat, message, setMessage, lastRun, setLastRun, busy, mutate, hasCurrentStrategy }: { slug: string; readOnly: boolean; chat: StrategistChatState; message: string; setMessage: (value: string) => void; lastRun: StrategistRunResponse | null; setLastRun: (value: StrategistRunResponse | null) => void; busy: string | null; mutate: (key: string, action: () => Promise<unknown>, success: string) => Promise<boolean>; hasCurrentStrategy: boolean }) {
  const runModes = [
    ["generate-initial", "Generate initial guidance"],
    ["recommend", "Recommend next"],
    ["reassess", "Reassess"],
    ["review-completion", "Review completion"],
  ] as const;
  const runBusy = runModes.some(([mode]) => busy === `strategist-${mode}`);
  return (
    <section className="rounded-lg border border-violet-500/30 bg-violet-500/5 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Engagement Strategist</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Personal conversation over the shared dossier. Recommendations create proposals; they never execute tools or silently change strategy.
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {runModes.map(([mode, label]) => (
            <Button
              key={mode}
              size="sm"
              variant="outline"
              disabled={readOnly || busy !== null || (!hasCurrentStrategy && mode !== "generate-initial")}
              onClick={() =>
                void mutate(
                  `strategist-${mode}`,
                  async () => {
                    setLastRun(await runEngagementStrategist(slug, mode));
                  },
                  `${label} strategist run completed. Review proposals before accepting them.`,
                )
              }
            >
              {busy === `strategist-${mode}` ? "Running…" : label}
            </Button>
          ))}
        </div>
      </div>

      {runBusy && (
        <div className="mt-4 rounded border border-violet-500/30 bg-background/70 p-3 text-sm">
          <p className="font-medium">Engagement Strategist is working…</p>
          <p className="mt-1 text-xs text-muted-foreground">Building context, checking current findings and work, and preparing durable proposals for Needs decision.</p>
        </div>
      )}

      {lastRun && (
        <details open className="mt-4 rounded border border-violet-500/20 bg-background/70 p-3">
          <summary className="cursor-pointer text-sm font-medium">Latest strategist output</summary>
          <p className="mt-2 whitespace-pre-wrap text-sm">{lastRun.output.situation_summary}</p>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <FactCard title={`Facts (${lastRun.output.facts.length})`} value={lastRun.output.facts.map((row) => row.statement)} />
            <FactCard title={`Inferences (${lastRun.output.inferences.length})`} value={lastRun.output.inferences.map((row) => `${row.confidence}: ${row.statement}`)} />
            <FactCard title={`Hypotheses (${lastRun.output.hypotheses.length})`} value={lastRun.output.hypotheses.map((row) => `${row.statement} — validate: ${row.validation_needed}`)} />
            <FactCard title={`Coverage gaps (${lastRun.output.coverage_gaps.length})`} value={lastRun.output.coverage_gaps} />
          </div>
          {(lastRun.output.work_item_proposals.length > 0 || lastRun.output.strategy_revision_proposal) && (
            <div className="mt-3 rounded border border-amber-500/30 bg-amber-500/5 p-3 text-xs">
              <p className="font-medium">Proposals awaiting analyst action</p>
              <ul className="mt-2 space-y-1 text-muted-foreground">
                {lastRun.output.work_item_proposals.map((proposal) => <li key={proposal.proposal_key}>• Work: {proposal.title}</li>)}
                {lastRun.output.strategy_revision_proposal && <li>• Strategy: {lastRun.output.strategy_revision_proposal.summary ?? "Proposed revision"}</li>}
              </ul>
              <p className="mt-2">Open proposals appear in Needs decision. Generating output does not accept them.</p>
            </div>
          )}
          {lastRun.output.warnings.length > 0 && <div className="mt-3"><FactCard title="Warnings" value={lastRun.output.warnings} /></div>}
          <p className="mt-2 font-mono text-[10px] text-muted-foreground">execution {lastRun.execution_id} · context {lastRun.context_hash.slice(0, 12)}…</p>
        </details>
      )}

      <div className="mt-4 rounded border border-border bg-background/70 p-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h4 className="text-xs font-medium">Personal strategist conversation</h4>
            <p className="text-[10px] text-muted-foreground">Raw chat is visible only to you. Accepted actions become shared records.</p>
          </div>
          {!readOnly && (
            <div className="flex gap-2">
              <SmallAction onClick={() => void mutate("strategist-summarize", () => summarizeStrategistChat(slug), "Strategist conversation summarized into the audit ledger.")}>Summarize</SmallAction>
              <SmallAction onClick={() => { if (window.confirm("Clear your personal strategist conversation? Shared accepted records and audit history remain.")) void mutate("strategist-clear", () => clearStrategistChat(slug), "Personal strategist conversation cleared."); }}>Clear</SmallAction>
            </div>
          )}
        </div>
        <div className="mt-3 max-h-[34rem] space-y-3 overflow-y-auto pr-1">
          {chat.messages.length === 0 ? (
            <p className="rounded border border-dashed border-border p-3 text-xs text-muted-foreground">No strategist conversation yet. Ask for priorities, gaps, or a proposed next move.</p>
          ) : (
            chat.messages.map((row) => {
              const actions = row.action_payload?.actions ?? [];
              return (
                <div key={row.id} className={cn("rounded border p-3 text-sm", row.role === "user" ? "ml-8 border-primary/30 bg-primary/5" : "mr-8 border-border bg-card")}>
                  <div className="flex justify-between gap-2 text-[10px] uppercase text-muted-foreground"><span>{row.role === "user" ? "Analyst" : "Strategist"}</span><DateTime value={row.created_at} /></div>
                  <p className="mt-1 whitespace-pre-wrap">{row.content}</p>
                  {actions.length > 0 && (
                    <div className="mt-3 rounded border border-amber-500/30 bg-amber-500/5 p-2 text-xs">
                      <p className="font-medium">Proposals created</p>
                      <ul className="mt-1 space-y-1 text-muted-foreground">
                        {actions.map((action, index) => (
                          <li key={`${action.suggestion_id}-${index}`}>• {action.title} — {labelSuggestionKind(action.suggestion_kind as Suggestion["kind"])} · {action.status}</li>
                        ))}
                      </ul>
                      <p className="mt-2 text-[10px] text-muted-foreground">Accept or dismiss these from Needs decision so they survive chat cleanup.</p>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
        {!readOnly && (
          <div className="mt-3 flex gap-2">
            <Textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Ask the Engagement Strategist…" rows={3} />
            <Button
              className="self-end"
              disabled={!message.trim() || busy !== null}
              onClick={() =>
                void (async () => {
                  const text = message.trim();
                  if (
                    await mutate(
                      "strategist-chat",
                      () => postStrategistChat(slug, text, chat.conversation_id),
                      "Strategist replied. Review any proposed actions explicitly.",
                    )
                  ) {
                    setMessage("");
                  }
                })()
              }
            >
              {busy === "strategist-chat" ? "Thinking…" : "Send"}
            </Button>
          </div>
        )}
      </div>
    </section>
  );
}

function CompletionSection({ slug, engagementStatus, readiness, decisions, exceptions, setExceptions, busy, mutate }: { slug: string; engagementStatus: EngagementStatus; readiness: CompletionReadiness; decisions: CompletionDecision[]; exceptions: Record<string, string>; setExceptions: (value: Record<string, string>) => void; busy: string | null; mutate: (key: string, action: () => Promise<unknown>, success: string) => Promise<boolean> }) {
  const latestApproval = decisions.find((decision) => decision.action === "approved");
  const acceptedExceptions: CompletionException[] = readiness.accepted_gap_candidates.flatMap((candidate) => { const key = `${candidate.ref.type}:${candidate.ref.id}`; const rationale = exceptions[key]?.trim(); return rationale ? [{ ref: candidate.ref, rationale }] : []; });
  return <section className="rounded-lg border border-border bg-card/40 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-sm font-semibold">Coverage and completion</h3><p className="text-xs text-muted-foreground">Work state: {readiness.work_state} · version {readiness.work_state_version}</p></div><Badge variant={readiness.ready ? "secondary" : "outline"}>{readiness.ready ? "ready" : "not ready"}</Badge></div><ul className="mt-3 grid gap-2 sm:grid-cols-2">{readiness.checks.filter((check) => check.count > 0).map((check) => <li key={check.key} className={cn("rounded border p-3 text-xs", check.severity === "blocker" ? "border-rose-500/40 bg-rose-500/10" : check.severity === "warning" ? "border-amber-500/40 bg-amber-500/10" : "border-border")}><div className="flex justify-between gap-2"><span className="font-medium">{check.key}</span><span>{check.count}</span></div><p className="mt-1 text-muted-foreground">{check.message}</p>{check.waivable && <span className="mt-1 inline-block text-[10px] uppercase text-muted-foreground">waivable</span>}</li>)}</ul>{readiness.accepted_gap_candidates.length > 0 && <div className="mt-4"><h4 className="text-xs font-medium">Accepted-gap rationales</h4><div className="mt-2 space-y-2">{readiness.accepted_gap_candidates.map((candidate) => { const key = `${candidate.ref.type}:${candidate.ref.id}`; return <label key={key} className="block rounded border border-border p-2 text-xs"><span>{candidate.message}</span><Input value={exceptions[key] ?? ""} onChange={(event) => setExceptions({ ...exceptions, [key]: event.target.value })} placeholder="Rationale required to accept this gap" className="mt-2 h-8 text-xs" /></label>; })}</div></div>}<div className="mt-4 flex flex-wrap justify-end gap-2">{engagementStatus === "active" && readiness.work_state === "active" && <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void mutate("completion-review", () => startCompletionReview(slug, readiness), "Completion review started.")}>Start completion review</Button>}{engagementStatus === "active" && readiness.work_state === "completion_review" && <Button size="sm" disabled={busy !== null} onClick={() => void mutate("completion-approve", () => approveCompletion(slug, readiness, acceptedExceptions), "Engagement completion approved.")}>Approve completion</Button>}{engagementStatus === "active" && readiness.work_state === "completed" && latestApproval && <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => { const reason = window.prompt("Reason for reopening this engagement"); if (reason?.trim()) void mutate("completion-reopen", () => reopenCompletion(slug, readiness, latestApproval.id, reason.trim()), "Engagement reopened."); }}>Reopen engagement</Button>}</div><details className="mt-4"><summary className="cursor-pointer text-xs font-medium">Completion decisions ({decisions.length})</summary><ul className="mt-2 space-y-1">{decisions.map((decision) => <li key={decision.id} className="rounded border border-border p-2 text-xs"><span className="font-medium">{decision.action}</span> · {decision.from_work_state} → {decision.to_work_state} · <DateTime value={decision.created_at} />{decision.reason && <p className="mt-1 text-muted-foreground">{decision.reason}</p>}</li>)}</ul></details></section>;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "warn" }) { return <div className={cn("rounded-lg border border-border bg-card/40 p-3", tone === "warn" && "border-amber-500/40 bg-amber-500/5")}><p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold">{value}</p></div>; }
function FactCard({ title, value }: { title: string; value: unknown }) { return <div className="rounded border border-sky-500/20 bg-background/60 p-3"><p className="text-xs font-medium">{title}</p><div className="mt-2 text-xs text-muted-foreground"><JsonSummary value={value} /></div></div>; }
function JsonSummary({ value }: { value: unknown }) { if (value === null || value === undefined) return <span>None</span>; if (Array.isArray(value)) return value.length ? <ul className="space-y-1">{value.slice(0, 8).map((item, index) => <li key={index}>• {typeof item === "string" ? item : JSON.stringify(item)}</li>)}</ul> : <span>None</span>; if (typeof value === "object") return <dl className="space-y-1">{Object.entries(value as Record<string, unknown>).slice(0, 10).map(([key, item]) => <div key={key} className="flex justify-between gap-3"><dt>{key.replaceAll("_", " ")}</dt><dd className="text-right text-foreground">{typeof item === "object" ? JSON.stringify(item) : String(item)}</dd></div>)}</dl>; return <span>{String(value)}</span>; }
function SmallAction({ children, onClick, disabled }: { children: React.ReactNode; onClick: () => void; disabled?: boolean }) { return <button type="button" onClick={onClick} disabled={disabled} className="rounded border border-border px-2 py-1 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50">{children}</button>; }
function messageFor(reason: unknown): string { return reason instanceof Error ? reason.message : String(reason); }
