"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { DateTime } from "@/components/date-time";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  approveCompletion,
  blockWorkItem,
  cancelObjective,
  createCheckpoint,
  createCoverageItem,
  createObjective,
  createStrategyRevision,
  createWorkItem,
  decideStrategyRevision,
  decideStrategySignal,
  getCompletionReadiness,
  getCurrentStrategy,
  getResumeBriefing,
  listCheckpoints,
  listCompletionDecisions,
  listCoverage,
  listObjectives,
  listStrategyRevisions,
  listStrategySignals,
  listWorkItems,
  reopenCompletion,
  resolveWorkItem,
  startCompletionReview,
  transitionObjective,
  transitionWorkItem,
  updateCoverageItem,
  updateObjective,
  updateWorkItem,
} from "@/lib/strategy-api";
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
  StrategyRevision,
  StrategySignal,
  WorkItem,
  WorkItemExecutor,
  WorkItemPriority,
  WorkItemResolution,
  WorkItemStatus,
} from "@/lib/strategy-types";
import type { EngagementStatus } from "@/lib/types";
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

export function StrategyView({
  slug,
  engagementStatus,
}: {
  slug: string;
  engagementStatus: EngagementStatus;
}) {
  const [data, setData] = useState<LoadState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [strategyBody, setStrategyBody] = useState("");
  const [strategySummary, setStrategySummary] = useState("");
  const [objectiveTitle, setObjectiveTitle] = useState("");
  const [objectivePriority, setObjectivePriority] = useState<ObjectivePriority>("medium");
  const [workTitle, setWorkTitle] = useState("");
  const [workPriority, setWorkPriority] = useState<WorkItemPriority>("medium");
  const [workExecutor, setWorkExecutor] = useState<WorkItemExecutor>("unassigned");
  const [workObjective, setWorkObjective] = useState("");
  const [workStatusFilter, setWorkStatusFilter] = useState<WorkItemStatus | "all">("all");
  const [workQuery, setWorkQuery] = useState("");
  const [selectedWorkId, setSelectedWorkId] = useState<string | null>(null);
  const [resolution, setResolution] = useState<WorkItemResolution>("completed");
  const [checkpointNarrative, setCheckpointNarrative] = useState("");
  const [coverageTarget, setCoverageTarget] = useState("");
  const [coverageKind, setCoverageKind] = useState("domain");
  const [coverageCategory, setCoverageCategory] = useState("scope_review");
  const [completionExceptions, setCompletionExceptions] = useState<
    Record<string, string>
  >({});

  const refresh = useCallback(async () => {
    setError(null);
    const [
      current,
      revisions,
      objectives,
      workItems,
      signals,
      checkpoints,
      coverage,
      completion,
      decisions,
      resume,
    ] = await Promise.all([
      getCurrentStrategy(slug),
      listStrategyRevisions(slug),
      listObjectives(slug),
      listWorkItems(slug),
      listStrategySignals(slug),
      listCheckpoints(slug),
      listCoverage(slug),
      getCompletionReadiness(slug),
      listCompletionDecisions(slug),
      getResumeBriefing(slug),
    ]);
    setData({
      current,
      revisions,
      objectives,
      workItems,
      signals,
      checkpoints,
      coverage,
      completion,
      decisions,
      resume,
    });
    setStrategyBody((previous) => previous || current?.body || "");
    setStrategySummary((previous) => previous || current?.summary || "");
    setSelectedWorkId((previous) =>
      previous && workItems.some((item) => item.id === previous)
        ? previous
        : workItems[0]?.id ?? null,
    );
  }, [slug]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    refresh()
      .catch((reason) => {
        if (active) setError(messageFor(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refresh]);

  const mutate = useCallback(
    async (key: string, action: () => Promise<unknown>, success: string) => {
      setBusy(key);
      setError(null);
      setNotice(null);
      try {
        await action();
        await refresh();
        setNotice(success);
        return true;
      } catch (reason) {
        setError(messageFor(reason));
        return false;
      } finally {
        setBusy(null);
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

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading engagement strategy…</p>;
  }
  if (!data) {
    return <p className="text-sm text-critical">{error ?? "Strategy workspace unavailable."}</p>;
  }

  const readOnly =
    engagementStatus !== "active" || data.completion.work_state !== "active";
  const remaining = data.workItems.filter((item) =>
    ["ready", "in_progress", "blocked"].includes(item.status),
  ).length;
  const blocked = data.workItems.filter((item) => item.status === "blocked").length;
  const deferred = data.workItems.filter((item) => item.status === "deferred").length;
  const openSignals = data.signals.filter((signal) => signal.status === "open");
  const nonSignalDecisions = data.resume.decisions_required.filter(
    (decision) => decision.type !== "strategy_signal",
  );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">Strategy</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Shared direction, committed work, coverage, decisions, and intentional closure.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={busy !== null}>
          Refresh
        </Button>
      </header>

      {readOnly && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-200">
          {engagementStatus !== "active"
            ? `This engagement is ${engagementStatus}. Strategy and work are read-only.`
            : `Engagement work state is ${data.completion.work_state}. Reopen or finish the completion decision before changing strategy or work.`}
        </div>
      )}
      {error && <p role="alert" className="rounded-md border border-critical/40 bg-critical/10 p-3 text-sm text-critical">{error}</p>}
      {notice && <p role="status" className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-200">{notice}</p>}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Metric label="Strategy" value={data.current ? `v${data.current.version}` : "Not set"} />
        <Metric label="Remaining work" value={String(remaining)} />
        <Metric label="Blocked" value={String(blocked)} tone={blocked ? "warn" : undefined} />
        <Metric label="Deferred" value={String(deferred)} />
        <Metric label="Decisions" value={String(nonSignalDecisions.length + openSignals.length)} />
      </section>

      <ResumeSection resume={data.resume} slug={slug} />

      <section className="rounded-lg border border-border bg-card/40 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">Current strategy</h3>
            <p className="text-xs text-muted-foreground">
              {data.current ? `Version ${data.current.version} · ${data.current.state}` : "Create the first shared revision."}
            </p>
          </div>
          {data.current && <DateTime value={data.current.updated_at} />}
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
                  <div className="mt-2 flex justify-end gap-2">
                    <SmallAction onClick={() => void mutate(`revision-${revision.id}-reject`, () => decideStrategyRevision(slug, revision.id, "reject", { based_on_revision_id: data.current?.id ?? null }), `Revision v${revision.version} rejected.`)}>Reject</SmallAction>
                    <SmallAction onClick={() => void mutate(`revision-${revision.id}-accept`, () => decideStrategyRevision(slug, revision.id, "accept", { based_on_revision_id: data.current?.id ?? null }), `Revision v${revision.version} accepted.`)}>Accept</SmallAction>
                  </div>
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
        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,0.8fr)]">
          <ul className="max-h-[38rem] space-y-2 overflow-y-auto pr-1">
            {visibleWork.map((item) => (
              <li key={item.id}>
                <button type="button" onClick={() => setSelectedWorkId(item.id)} className={cn("w-full rounded border p-3 text-left hover:bg-muted/40", selectedWorkId === item.id ? "border-foreground/40 bg-muted/30" : "border-border bg-background")}>
                  <div className="flex items-start justify-between gap-2"><span className="font-medium">{item.title}</span><span className="flex gap-1"><Badge variant="outline">{item.priority}</Badge><Badge variant="secondary">{item.status}</Badge></span></div>
                  <p className="mt-1 text-xs text-muted-foreground">{item.executor_type}{item.due_at ? <> · due <DateTime value={item.due_at} /></> : null}</p>
                  {item.blocked_reason && <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">Blocked: {item.blocked_reason}</p>}
                </button>
              </li>
            ))}
            {visibleWork.length === 0 && <li className="text-sm text-muted-foreground">No matching work.</li>}
          </ul>
          <WorkDetail item={selectedWork} objectives={data.objectives} slug={slug} readOnly={readOnly} busy={busy} resolution={resolution} setResolution={setResolution} mutate={mutate} />
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-lg border border-border bg-card/40 p-4">
          <h3 className="text-sm font-semibold">Needs decision</h3>
          {nonSignalDecisions.length === 0 && openSignals.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">No shared decisions are waiting.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {nonSignalDecisions.map((decision, index) => (
                <li key={String(decision.id ?? index)} className="rounded border border-border bg-background p-3 text-xs"><JsonSummary value={decision} /></li>
              ))}
              {openSignals.map((signal) => (
                <li key={signal.id} className="rounded border border-border bg-background p-3 text-xs">
                  <div className="flex items-start justify-between gap-2"><div><p className="font-medium">{signal.signal_type}</p><p className="mt-1 text-muted-foreground">{signal.summary}</p></div><Badge variant="outline">{signal.confidence}</Badge></div>
                  {!readOnly && <div className="mt-2 flex justify-end gap-2"><SmallAction onClick={() => void mutate(`signal-${signal.id}-dismiss`, () => decideStrategySignal(signal.id, "dismiss", window.prompt("Dismissal reason") ?? undefined), "Signal dismissed.")}>Dismiss</SmallAction><SmallAction onClick={() => void mutate(`signal-${signal.id}-incorporate`, () => decideStrategySignal(signal.id, "incorporate", window.prompt("How was this incorporated?") ?? undefined), "Signal incorporated.")}>Incorporate</SmallAction></div>}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-lg border border-border bg-card/40 p-4">
          <h3 className="text-sm font-semibold">Checkpoints</h3>
          {!readOnly && <div className="mt-3 flex gap-2"><Input value={checkpointNarrative} onChange={(event) => setCheckpointNarrative(event.target.value)} placeholder="Optional end-of-session note" /><Button size="sm" onClick={() => void (async () => { if (await mutate("checkpoint-create", () => createCheckpoint(slug, checkpointNarrative.trim() || undefined), "Checkpoint created.")) setCheckpointNarrative(""); })()}>Create</Button></div>}
          <ul className="mt-3 max-h-64 space-y-2 overflow-y-auto">
            {data.checkpoints.map((checkpoint) => <li key={checkpoint.id} className="rounded border border-border bg-background p-3 text-xs"><div className="flex justify-between"><span className="font-medium">Checkpoint</span><DateTime value={checkpoint.created_at} /></div><p className="mt-1 text-muted-foreground">{checkpoint.narrative ?? "Deterministic state snapshot"}</p></li>)}
            {data.checkpoints.length === 0 && <li className="text-sm text-muted-foreground">No checkpoints yet.</li>}
          </ul>
        </div>
      </section>

      <section className="rounded-lg border border-border bg-card/40 p-4">
        <h3 className="text-sm font-semibold">Coverage</h3>
        {!readOnly && <div className="mt-3 grid gap-2 sm:grid-cols-[8rem_1fr_13rem_auto]"><Input value={coverageKind} onChange={(event) => setCoverageKind(event.target.value)} placeholder="Target kind" /><Input value={coverageTarget} onChange={(event) => setCoverageTarget(event.target.value)} placeholder="Target key" /><select value={coverageCategory} onChange={(event) => setCoverageCategory(event.target.value)} className="rounded border border-input bg-background px-2 text-sm">{COVERAGE_CATEGORIES.map((category) => <option key={category}>{category}</option>)}</select><Button size="sm" disabled={!coverageTarget.trim()} onClick={() => void (async () => { if (await mutate("coverage-create", () => createCoverageItem(slug, { target_kind: coverageKind.trim(), target_key: coverageTarget.trim(), activity_category: coverageCategory }), "Coverage item created.")) setCoverageTarget(""); })()}>Add</Button></div>}
        <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr className="border-b border-border text-muted-foreground"><th className="py-2">Target</th><th>Category</th><th>Status</th><th>Reason</th></tr></thead><tbody>{data.coverage.map((item) => <tr key={item.id} className="border-b border-border/60"><td className="py-2 font-mono">{item.target_kind}:{item.target_key}</td><td>{item.activity_category}</td><td>{readOnly ? <Badge variant="outline">{item.status}</Badge> : <select value={item.status} onChange={(event) => { const next = event.target.value as CoverageStatus; const reason = next === "accepted_gap" ? window.prompt("Accepted gap rationale (required)") ?? "" : item.reason ?? ""; if (next === "accepted_gap" && !reason.trim()) return; void mutate(`coverage-${item.id}`, () => updateCoverageItem(slug, item, next, reason), "Coverage updated."); }} className="h-8 rounded border border-input bg-background px-2">{COVERAGE_STATUSES.map((status) => <option key={status}>{status}</option>)}</select>}</td><td className="max-w-xs truncate">{item.reason ?? "—"}</td></tr>)}</tbody></table>{data.coverage.length === 0 && <p className="py-3 text-sm text-muted-foreground">No coverage rows yet.</p>}</div>
      </section>

      <CompletionSection slug={slug} engagementStatus={engagementStatus} readiness={data.completion} decisions={data.decisions} exceptions={completionExceptions} setExceptions={setCompletionExceptions} busy={busy} mutate={mutate} />
    </div>
  );
}

function ResumeSection({ resume, slug }: { resume: ResumeBriefing; slug: string }) {
  return (
    <section className="rounded-lg border border-sky-500/30 bg-sky-500/5 p-4">
      <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold">Resume engagement</h3><p className="text-xs text-muted-foreground">Deterministic briefing · <DateTime value={resume.generated_at} /></p></div><Link href={`/e?slug=${encodeURIComponent(slug)}&view=status`} className="text-xs hover:underline">Open execution Status →</Link></div>
      <div className="mt-3 grid gap-3 lg:grid-cols-2"><FactCard title="Current focus" value={resume.current_focus} /><FactCard title="Since checkpoint" value={resume.since_checkpoint} /><FactCard title={`Active work (${resume.active_work.length})`} value={resume.active_work.slice(0, 5).map((item) => item.title)} /><FactCard title={`Blocked work (${resume.blocked_work.length})`} value={resume.blocked_work.slice(0, 5).map((item) => item.title)} /></div>
    </section>
  );
}

function WorkDetail({ item, objectives, slug, readOnly, busy, resolution, setResolution, mutate }: { item: WorkItem | null; objectives: Objective[]; slug: string; readOnly: boolean; busy: string | null; resolution: WorkItemResolution; setResolution: (value: WorkItemResolution) => void; mutate: (key: string, action: () => Promise<unknown>, success: string) => Promise<boolean> }) {
  if (!item) return <div className="rounded border border-dashed border-border p-4 text-sm text-muted-foreground">Select a work item.</div>;
  const objective = objectives.find((row) => row.id === item.objective_id);
  const terminal = item.status === "completed" || item.status === "cancelled";
  return <article className="rounded border border-border bg-background p-4"><div className="flex items-start justify-between gap-2"><div><h4 className="font-semibold">{item.title}</h4><p className="mt-1 text-xs text-muted-foreground">row v{item.row_version} · {item.executor_type}{objective ? ` · ${objective.title}` : ""}</p></div><Badge variant="outline">{item.status}</Badge></div>{item.description && <p className="mt-3 whitespace-pre-wrap text-sm">{item.description}</p>}{item.rationale && <p className="mt-3 text-xs text-muted-foreground">Rationale: {item.rationale}</p>}{item.acceptance_criteria.length > 0 && <div className="mt-3"><p className="text-xs font-medium">Acceptance criteria</p><ul className="mt-1 list-disc pl-5 text-xs text-muted-foreground">{item.acceptance_criteria.map((criterion, index) => <li key={index}>{criterion}</li>)}</ul></div>}{item.finding_links.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{item.finding_links.map((link) => <Link key={`${link.finding_id}-${link.relationship}`} href={`/e/findings/${link.finding_id}?slug=${encodeURIComponent(slug)}&returnTo=${encodeURIComponent(`/e?slug=${slug}&view=strategy&workItem=${item.id}`)}`} className="rounded-full border border-border px-2 py-1 text-[10px] hover:underline">Finding · {link.relationship}</Link>)}</div>}{!readOnly && <div className="mt-4 space-y-2 border-t border-border pt-3"><div className="flex flex-wrap gap-2">{(item.status === "ready" || item.status === "blocked") && <SmallAction onClick={() => void mutate(`work-${item.id}-start`, () => transitionWorkItem(item.id, "start", item.row_version), "Work started.")}>Start</SmallAction>}{item.status === "in_progress" && <SmallAction onClick={() => { const reason = window.prompt("Blocking reason"); if (reason?.trim()) void mutate(`work-${item.id}-block`, () => blockWorkItem(item.id, item.row_version, reason.trim()), "Work blocked."); }}>Block</SmallAction>}{!terminal && item.status !== "deferred" && <SmallAction onClick={() => void mutate(`work-${item.id}-defer`, () => transitionWorkItem(item.id, "defer", item.row_version, window.prompt("Deferral reason") ?? undefined), "Work deferred.")}>Defer</SmallAction>}{(item.status === "deferred" || item.status === "completed") && <SmallAction onClick={() => void mutate(`work-${item.id}-reopen`, () => transitionWorkItem(item.id, "reopen", item.row_version), "Work reopened.")}>Reopen</SmallAction>}{!terminal && <SmallAction onClick={() => void mutate(`work-${item.id}-cancel`, () => transitionWorkItem(item.id, "cancel", item.row_version, window.prompt("Cancellation reason") ?? undefined), "Work cancelled.")}>Cancel</SmallAction>}<SmallAction onClick={() => { const title = window.prompt("Work item title", item.title); if (title?.trim()) void mutate(`work-${item.id}-edit`, () => updateWorkItem(item.id, { expected_row_version: item.row_version, title: title.trim() }), "Work item updated."); }}>Edit</SmallAction></div>{!terminal && <div className="flex gap-2"><select value={resolution} onChange={(event) => setResolution(event.target.value as WorkItemResolution)} className="h-8 rounded border border-input bg-background px-2 text-xs">{RESOLUTIONS.map((outcome) => <option key={outcome}>{outcome}</option>)}</select><Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void mutate(`work-${item.id}-resolve`, () => resolveWorkItem(item.id, item.row_version, resolution, window.prompt("Resolution note") ?? undefined), "Work resolved.")}>Resolve</Button></div>}</div>}</article>;
}

function CompletionSection({ slug, engagementStatus, readiness, decisions, exceptions, setExceptions, busy, mutate }: { slug: string; engagementStatus: EngagementStatus; readiness: CompletionReadiness; decisions: CompletionDecision[]; exceptions: Record<string, string>; setExceptions: (value: Record<string, string>) => void; busy: string | null; mutate: (key: string, action: () => Promise<unknown>, success: string) => Promise<boolean> }) {
  const latestApproval = decisions.find((decision) => decision.action === "approved");
  const acceptedExceptions: CompletionException[] = readiness.accepted_gap_candidates.flatMap((candidate) => { const key = `${candidate.ref.type}:${candidate.ref.id}`; const rationale = exceptions[key]?.trim(); return rationale ? [{ ref: candidate.ref, rationale }] : []; });
  return <section className="rounded-lg border border-border bg-card/40 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-sm font-semibold">Coverage and completion</h3><p className="text-xs text-muted-foreground">Work state: {readiness.work_state} · version {readiness.work_state_version}</p></div><Badge variant={readiness.ready ? "secondary" : "outline"}>{readiness.ready ? "ready" : "not ready"}</Badge></div><ul className="mt-3 grid gap-2 sm:grid-cols-2">{readiness.checks.filter((check) => check.count > 0).map((check) => <li key={check.key} className={cn("rounded border p-3 text-xs", check.severity === "blocker" ? "border-rose-500/40 bg-rose-500/10" : check.severity === "warning" ? "border-amber-500/40 bg-amber-500/10" : "border-border")}><div className="flex justify-between gap-2"><span className="font-medium">{check.key}</span><span>{check.count}</span></div><p className="mt-1 text-muted-foreground">{check.message}</p>{check.waivable && <span className="mt-1 inline-block text-[10px] uppercase text-muted-foreground">waivable</span>}</li>)}</ul>{readiness.accepted_gap_candidates.length > 0 && <div className="mt-4"><h4 className="text-xs font-medium">Accepted-gap rationales</h4><div className="mt-2 space-y-2">{readiness.accepted_gap_candidates.map((candidate) => { const key = `${candidate.ref.type}:${candidate.ref.id}`; return <label key={key} className="block rounded border border-border p-2 text-xs"><span>{candidate.message}</span><Input value={exceptions[key] ?? ""} onChange={(event) => setExceptions({ ...exceptions, [key]: event.target.value })} placeholder="Rationale required to accept this gap" className="mt-2 h-8 text-xs" /></label>; })}</div></div>}<div className="mt-4 flex flex-wrap justify-end gap-2">{engagementStatus === "active" && readiness.work_state === "active" && <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void mutate("completion-review", () => startCompletionReview(slug, readiness), "Completion review started.")}>Start completion review</Button>}{engagementStatus === "active" && readiness.work_state === "completion_review" && <Button size="sm" disabled={busy !== null} onClick={() => void mutate("completion-approve", () => approveCompletion(slug, readiness, acceptedExceptions), "Engagement completion approved.")}>Approve completion</Button>}{engagementStatus === "active" && readiness.work_state === "completed" && latestApproval && <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => { const reason = window.prompt("Reason for reopening this engagement"); if (reason?.trim()) void mutate("completion-reopen", () => reopenCompletion(slug, readiness, latestApproval.id, reason.trim()), "Engagement reopened."); }}>Reopen engagement</Button>}</div><details className="mt-4"><summary className="cursor-pointer text-xs font-medium">Completion decisions ({decisions.length})</summary><ul className="mt-2 space-y-1">{decisions.map((decision) => <li key={decision.id} className="rounded border border-border p-2 text-xs"><span className="font-medium">{decision.action}</span> · {decision.from_work_state} → {decision.to_work_state} · <DateTime value={decision.created_at} />{decision.reason && <p className="mt-1 text-muted-foreground">{decision.reason}</p>}</li>)}</ul></details></section>;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "warn" }) { return <div className={cn("rounded-lg border border-border bg-card/40 p-3", tone === "warn" && "border-amber-500/40 bg-amber-500/5")}><p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold">{value}</p></div>; }
function FactCard({ title, value }: { title: string; value: unknown }) { return <div className="rounded border border-sky-500/20 bg-background/60 p-3"><p className="text-xs font-medium">{title}</p><div className="mt-2 text-xs text-muted-foreground"><JsonSummary value={value} /></div></div>; }
function JsonSummary({ value }: { value: unknown }) { if (value === null || value === undefined) return <span>None</span>; if (Array.isArray(value)) return value.length ? <ul className="space-y-1">{value.slice(0, 8).map((item, index) => <li key={index}>• {typeof item === "string" ? item : JSON.stringify(item)}</li>)}</ul> : <span>None</span>; if (typeof value === "object") return <dl className="space-y-1">{Object.entries(value as Record<string, unknown>).slice(0, 10).map(([key, item]) => <div key={key} className="flex justify-between gap-3"><dt>{key.replaceAll("_", " ")}</dt><dd className="text-right text-foreground">{typeof item === "object" ? JSON.stringify(item) : String(item)}</dd></div>)}</dl>; return <span>{String(value)}</span>; }
function SmallAction({ children, onClick }: { children: React.ReactNode; onClick: () => void }) { return <button type="button" onClick={onClick} className="rounded border border-border px-2 py-1 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{children}</button>; }
function messageFor(reason: unknown): string { return reason instanceof Error ? reason.message : String(reason); }
