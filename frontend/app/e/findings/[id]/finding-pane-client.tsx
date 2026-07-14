"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Activity,
  ArrowLeft,
  Bot,
  CheckCircle2,
  CircleDot,
  ListTodo,
  MessageSquare,
  Sparkles,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { DateTime } from "@/components/date-time";
import { Badge } from "@/components/ui/badge";
import {
  createFindingSummary,
  createObservation,
  deleteAttachment,
  deleteFinding,
  linkObservationFinding,
  listAttachments,
  listFindingSummaries,
  listFindings,
  listObservationsForFinding,
  listScope,
  listTasks,
  loadAttachmentBlob,
  rewriteFindingSummary,
  triageFinding,
  updateFinding,
  uploadAttachment,
  validateFinding,
  ApiError,
} from "@/lib/api";
import {
  useAcceptFindingChatActionMutation,
  useDenyFindingChatActionMutation,
  useAskFindingChatMutation,
  useClearFindingChatMutation,
  useSummarizeFindingChatMutation,
  useFinding,
  useFindingActivity,
  useFindingContext,
  useFindings,
  usePromoteFindingContextMutation,
  qk,
  useFindingChat,
} from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { LoaderOverlay } from "@/components/loader";
import { GroupedItemsView, extractItems } from "@/components/grouped-items-view";
import type {
  Attachment,
  Finding,
  FindingExclusion,
  FindingActivityEntry,
  FindingChatAction,
  FindingChatMessage,
  FindingPhase,
  FindingSummaryEntry,
  FindingValidationStatus,
  Observation,
  ScopeItem,
  Severity,
  Task,
} from "@/lib/types";

const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "border-rose-500/50 bg-rose-500/15 text-rose-700 dark:text-rose-200",
  high: "border-pink-400/50 bg-pink-400/15 text-pink-700 dark:text-pink-200",
  medium: "border-yellow-400/50 bg-yellow-400/15 text-yellow-800 dark:text-yellow-100",
  low: "border-emerald-500/50 bg-emerald-500/15 text-emerald-700 dark:text-emerald-200",
  info: "border-sky-500/50 bg-sky-500/15 text-sky-700 dark:text-sky-200",
};

const STATUS_LABEL: Record<FindingValidationStatus, string> = {
  pending_validation: "Pending",
  validated: "Validated",
  rejected: "Rejected",
  false_positive: "False positive",
  needs_review: "Needs review",
};

const PHASE_LABEL: Record<FindingPhase, string> = {
  osint: "OSINT",
  vuln_scan: "Vuln Scan",
  exploit: "Exploit",
  phishing: "Phishing",
  general: "General",
};

// timeline kind → (icon, tint)
const KIND_META: Record<string, { icon: typeof Activity; tint: string }> = {
  created: { icon: CircleDot, tint: "text-sky-500" },
  task: { icon: ListTodo, tint: "text-violet-500" },
  agent_run: { icon: Bot, tint: "text-emerald-500" },
  "finding.validated": { icon: CheckCircle2, tint: "text-emerald-500" },
  "finding.triaged": { icon: Sparkles, tint: "text-amber-500" },
  "finding.summary_rewritten": { icon: Sparkles, tint: "text-amber-500" },
  "finding.summary_recorded": { icon: MessageSquare, tint: "text-muted-foreground" },
  "finding.updated": { icon: Activity, tint: "text-muted-foreground" },
};

function FindingPane({ id, slug }: { id: string; slug: string | null }) {
  const { data: finding, isLoading, error } = useFinding(id);
  const { data: activity } = useFindingActivity(id);

  if (isLoading) {
    return (
      <p className="px-6 py-10 text-sm text-muted-foreground">
        Loading finding…
      </p>
    );
  }
  if (error || !finding) {
    return (
      <p className="px-6 py-10 text-sm text-critical">
        {error instanceof Error ? error.message : "Finding not found."}
      </p>
    );
  }

  const entries = activity ?? [];

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      {/* top bar */}
      <div className="mb-4 flex items-center justify-between gap-3">
        {slug ? (
          <Link
            href={`/e?slug=${encodeURIComponent(slug)}`}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> back to engagement
          </Link>
        ) : (
          <Link
            href="/"
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            ← engagements
          </Link>
        )}
        <span className="font-mono text-[10px] text-muted-foreground">
          {finding.id}
        </span>
      </div>

      {/* header */}
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant="outline"
            className={cn("border", SEVERITY_CLASS[finding.severity])}
          >
            {finding.severity}
          </Badge>
          <Badge variant="secondary" className="text-[10px]">
            {STATUS_LABEL[finding.status]}
          </Badge>
          <Badge variant="outline" className="text-[10px]">
            {PHASE_LABEL[finding.phase]}
          </Badge>
        </div>
        <h1 className="mt-3 text-xl font-semibold leading-tight">
          {finding.title}
        </h1>
        {finding.target && (
          <p className="mt-1 font-mono text-sm text-muted-foreground">
            target: {finding.target}
          </p>
        )}
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
          {finding.tool && <span>source: {finding.tool}</span>}
          <span>created <DateTime value={finding.created_at} /></span>
          {finding.observed_at && (
            <span>observed <DateTime value={finding.observed_at} /></span>
          )}
        </div>
        {finding.summary && (
          <p className="mt-3 text-sm text-foreground">{finding.summary}</p>
        )}
      </div>

      {/* grouped hits — visible on every tab when the finding has items
          (subdomains, open ports, live URLs, etc.). Uses the shared
          GroupedItemsView which auto-picks a primary field (subdomain /
          url / host / ...) and an optional group-by column (source /
          service / status_code / ...) so tool output stays readable. */}
      {findingHasItems(finding) && (
        <section className="mt-5 rounded-lg border border-border bg-card p-5">
          <GroupedItemsView
            items={extractItems(finding.data)}
            headerLabel="Items"
            headerNote={
              finding.group_key
                ? "Every re-run of this tool against the same target folds here — hits are deduped by their natural key."
                : undefined
            }
            maxHeight="60vh"
          />
          {finding.group_key && (
            <p
              className="mt-2 truncate font-mono text-[10px] text-muted-foreground"
              title="Grouping key"
            >
              {finding.group_key}
            </p>
          )}
        </section>
      )}

      {/* two-column body: workbench left, activity rail right */}
      <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-4">
        <div className="xl:col-span-3">
          <FindingWorkbench finding={finding} slug={slug} />
        </div>

        <div className="xl:col-span-1">
          <ActivityRail entries={entries} />
        </div>
      </div>
    </div>
  );
}

function findingHasItems(finding: Finding): boolean {
  const items = (finding.data as { items?: unknown } | null | undefined)?.items;
  return Array.isArray(items) && items.length > 0;
}

function ActivityRail({ entries }: { entries: FindingActivityEntry[] }) {
  // Sticky-in-viewport + internally scrollable so long timelines don't
  // stretch the page. max-h leaves 1.5rem top clearance (matches `top-6`)
  // and 1.5rem bottom breathing room.
  return (
    <div className="sticky top-16 flex max-h-[calc(100vh-5rem)] flex-col rounded-lg border border-border bg-card/40 p-4">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-medium">
        <Activity className="h-4 w-4 text-muted-foreground" />
        Activity
        <span className="text-xs text-muted-foreground">({entries.length})</span>
      </h2>
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing recorded yet. Run a tool, triage, chat, or validate to populate
          this timeline.
        </p>
      ) : (
        <ol className="-mr-2 space-y-3 overflow-y-auto border-l border-border pl-4 pr-2">
          {entries.map((e, i) => (
            <TimelineRow key={`${e.ts}-${i}`} entry={e} />
          ))}
        </ol>
      )}
    </div>
  );
}

type WorkbenchTab = "notes" | "ai" | "evidence" | "details" | "tools";

const WORKBENCH_TABS: Array<{
  id: WorkbenchTab;
  label: string;
  description: string;
}> = [
  {
    id: "notes",
    label: "Notes",
    description: "Summary history, comments, tags, and reportability.",
  },
  {
    id: "ai",
    label: "AI",
    description: "Finding-scoped assistant for concise context and planning.",
  },
  {
    id: "evidence",
    label: "Evidence",
    description: "Attachments and artifacts supporting the finding.",
  },
  {
    id: "details",
    label: "Details",
    description: "Raw payload, timestamps, and normalized finding metadata.",
  },
  {
    id: "tools",
    label: "Tools",
    description: "Agent-executable enum/scan actions you can approve and dispatch.",
  },
];

function FindingWorkbench({
  finding,
  slug,
}: {
  finding: Finding;
  slug: string | null;
}) {
  const params = useSearchParams();
  const requestedTab = params?.get("tab") as WorkbenchTab | null;
  const [tab, setTab] = useState<WorkbenchTab>(
    requestedTab && WORKBENCH_TABS.some((item) => item.id === requestedTab)
      ? requestedTab
      : "notes",
  );
  const { data: chat } = useFindingChat(finding.id);
  const toolActionCount = openToolActions(chat?.messages ?? []).length;
  const active = WORKBENCH_TABS.find((t) => t.id === tab) ?? WORKBENCH_TABS[0];

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-card/40">
      <div className="border-b border-border bg-background/60 px-4 py-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-sm font-semibold">Finding workbench</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {active.description}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted/50 p-1 text-xs sm:flex">
            {WORKBENCH_TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                className={cn(
                  "rounded-md px-3 py-1.5 text-left font-medium transition-colors sm:text-center",
                  tab === item.id
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <span className="inline-flex items-center gap-1.5">
                  {item.label}
                  {item.id === "tools" && toolActionCount > 0 && (
                    <span className="rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-black">
                      {toolActionCount}
                    </span>
                  )}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="p-4">
        {tab === "ai" && <ChatRail findingId={finding.id} slug={slug} />}
        {tab === "notes" && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <DecisionPanel finding={finding} />
            <TagsPanel finding={finding} />
            <SummaryPanel finding={finding} />
            <CommentsPanel finding={finding} slug={slug} />
          </div>
        )}
        {tab === "evidence" && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <EvidenceChecklistPanel finding={finding} />
            <AttachmentsPanel finding={finding} />
          </div>
        )}
        {tab === "details" && (
          <div className="space-y-4">
            {slug && <ContextPromotionPanel finding={finding} slug={slug} />}
            <ScopeStatusPanel finding={finding} slug={slug} />
            <RelatedPanel finding={finding} slug={slug} />
            <ReportPreviewPanel finding={finding} />
            <DetailsPanel finding={finding} slug={slug} />
          </div>
        )}
        {tab === "tools" && <AgentToolsPanel findingId={finding.id} slug={slug} />}
      </div>
    </section>
  );
}

function DecisionPanel({ finding }: { finding: Finding }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function setStatus(status: FindingValidationStatus) {
    setBusy(true);
    setError(null);
    try {
      const updated = await validateFinding(finding.id, status);
      qc.setQueryData(qk.finding(finding.id), updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function setExclusion(exclusion: FindingExclusion | null) {
    setBusy(true);
    setError(null);
    try {
      const updated = await updateFinding(finding.id, { exclusion });
      qc.setQueryData(qk.finding(finding.id), updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="relative rounded-lg border border-border bg-card/40 p-4">
      <h2 className="text-sm font-medium">Decision</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Validation and reportability controls for this finding.
      </p>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <InfoTile label="Status" value={STATUS_LABEL[finding.status]} />
        <InfoTile label="Reportability" value={finding.exclusion ?? "included"} />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <SmallButton disabled={busy} onClick={() => setStatus("validated")}>
          Validate
        </SmallButton>
        <SmallButton disabled={busy} onClick={() => setStatus("rejected")}>
          Reject
        </SmallButton>
        <SmallButton disabled={busy} onClick={() => setStatus("false_positive")}>
          False positive
        </SmallButton>
        <SmallButton disabled={busy} onClick={() => setExclusion("out_of_scope")}>
          Out of scope
        </SmallButton>
        <SmallButton disabled={busy} onClick={() => setExclusion("outside_roe")}>
          Outside ROE
        </SmallButton>
        {finding.exclusion && (
          <SmallButton disabled={busy} onClick={() => setExclusion(null)}>
            Clear exclusion
          </SmallButton>
        )}
      </div>
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
      <LoaderOverlay show={busy} size={0.8} label="Updating decision" />
    </section>
  );
}

function SmallButton({
  children,
  disabled,
  onClick,
}: {
  children: ReactNode;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
    >
      {children}
    </button>
  );
}

function SummaryPanel({ finding }: { finding: Finding }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [rows, setRows] = useState<FindingSummaryEntry[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listFindingSummaries(finding.id)
      .then(setRows)
      .catch(() => setRows([]));
  }, [finding.id]);

  async function saveSummary() {
    const body = draft.trim();
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      const entry = await createFindingSummary(finding.id, body);
      setRows((prev) => [entry, ...(prev ?? [])]);
      setDraft("");
      qc.setQueryData<Finding>(qk.finding(finding.id), (prev) =>
        prev ? { ...prev, summary: entry.body } : prev,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function draftWithAi(mode: "triage" | "rewrite") {
    if (mode === "rewrite" && !draft.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = mode === "triage"
        ? await triageFinding(finding.id)
        : await rewriteFindingSummary(finding.id, draft.trim());
      setDraft(result.summary);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <h2 className="text-sm font-medium">Summary / analyst notes</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Latest report narrative plus immutable summary history.
      </p>
      {finding.summary ? (
        <p className="mt-3 rounded-md bg-background p-3 text-sm">
          {finding.summary}
        </p>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">No summary saved yet.</p>
      )}
      <textarea
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder="Add a new summary/history entry…"
        className="mt-3 min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={saveSummary}
          disabled={busy || !draft.trim()}
          className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
        >
          {busy ? "Working…" : "Save summary"}
        </button>
        <SmallButton disabled={busy} onClick={() => void draftWithAi("triage")}>
          AI Triage
        </SmallButton>
        <SmallButton disabled={busy || !draft.trim()} onClick={() => void draftWithAi("rewrite")}>
          AI Rewrite
        </SmallButton>
        {error && <span className="text-xs text-destructive">{error}</span>}
      </div>
      <div className="mt-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          History
        </h3>
        {rows === null ? (
          <p className="mt-2 text-xs text-muted-foreground">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="mt-2 text-xs text-muted-foreground">No history yet.</p>
        ) : (
          <ul className="mt-2 max-h-52 space-y-2 overflow-y-auto pr-1">
            {rows.map((entry) => (
              <li key={entry.id} className="rounded-md border border-border bg-background p-2">
                <p className="line-clamp-3 text-xs">{entry.body}</p>
                <p className="mt-1 text-[10px] text-muted-foreground">
                  {entry.author_display_name || entry.author_email || "unknown"} · <DateTime value={entry.created_at} />
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function TagsPanel({ finding }: { finding: Finding }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const tags = finding.tags ?? [];

  async function setTags(next: string[]) {
    setBusy(true);
    try {
      const updated = await updateFinding(finding.id, { tags: next });
      qc.setQueryData(qk.finding(finding.id), updated);
    } finally {
      setBusy(false);
    }
  }

  function addTag() {
    const tag = draft.trim();
    if (!tag || tags.includes(tag)) {
      setDraft("");
      return;
    }
    setDraft("");
    void setTags([...tags, tag]);
  }

  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <h2 className="text-sm font-medium">Tags & reportability</h2>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {tags.length === 0 ? (
          <span className="text-xs text-muted-foreground">No tags yet.</span>
        ) : (
          tags.map((tag) => (
            <span key={tag} className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2 py-0.5 text-xs">
              {tag}
              <button
                type="button"
                disabled={busy}
                onClick={() => setTags(tags.filter((t) => t !== tag))}
                className="text-muted-foreground hover:text-destructive"
              >
                ×
              </button>
            </span>
          ))
        )}
      </div>
      <div className="mt-3 flex gap-2">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addTag();
            }
          }}
          placeholder="Add tag"
          className="h-8 flex-1 rounded-md border border-input bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring"
        />
        <button
          type="button"
          onClick={addTag}
          disabled={busy || !draft.trim()}
          className="rounded-md border border-border px-3 py-1 text-xs disabled:opacity-50"
        >
          Add
        </button>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
        <InfoTile label="Status" value={STATUS_LABEL[finding.status]} />
        <InfoTile label="Reportability" value={finding.exclusion ?? "included"} />
      </div>
    </section>
  );
}

function CommentsPanel({ finding, slug }: { finding: Finding; slug: string | null }) {
  const [rows, setRows] = useState<Observation[] | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listObservationsForFinding(finding.id)
      .then(setRows)
      .catch(() => setRows([]));
  }, [finding.id]);

  async function addComment() {
    const content = draft.trim();
    if (!content || !slug) return;
    setBusy(true);
    setError(null);
    try {
      const obs = await createObservation(slug, {
        content,
        phase: finding.phase,
      });
      const linked = await linkObservationFinding(obs.id, finding.id);
      setRows((prev) => [...(prev ?? []), linked]);
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <h2 className="text-sm font-medium">Comments / observations</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Finding-linked observations from the engagement notebook.
      </p>
      <div className="mt-3 max-h-64 space-y-2 overflow-y-auto pr-1">
        {rows === null ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="text-xs text-muted-foreground">No comments yet.</p>
        ) : (
          rows.map((row) => (
            <div key={row.id} className="rounded-md border border-border bg-background p-2">
              <p className="text-sm">{row.content}</p>
              <p className="mt-1 text-[10px] text-muted-foreground"><DateTime value={row.created_at} /></p>
            </div>
          ))
        )}
      </div>
      <textarea
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder={slug ? "Add a comment / observation…" : "Open via an engagement slug to comment"}
        disabled={!slug || busy}
        className="mt-3 min-h-20 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
      />
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={addComment}
          disabled={!slug || busy || !draft.trim()}
          className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
        >
          {busy ? "Adding…" : "Add comment"}
        </button>
        {error && <span className="text-xs text-destructive">{error}</span>}
      </div>
    </section>
  );
}

function EvidenceChecklistPanel({ finding }: { finding: Finding }) {
  const checks = evidenceChecks(finding);
  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <h2 className="text-sm font-medium">Evidence checklist</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Quick report-readiness indicators for the finding record.
      </p>
      <ul className="mt-3 space-y-2">
        {checks.map((check) => (
          <li
            key={check.label}
            className="flex items-center justify-between rounded-md border border-border bg-background p-2 text-sm"
          >
            <span>{check.label}</span>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-[10px] font-medium",
                check.ok
                  ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-200"
                  : "bg-muted text-muted-foreground",
              )}
            >
              {check.ok ? "yes" : "missing"}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function evidenceChecks(finding: Finding): Array<{ label: string; ok: boolean }> {
  const blob = JSON.stringify({ data: finding.data, args: finding.args }).toLowerCase();
  return [
    { label: "Has affected target", ok: Boolean(finding.target) },
    { label: "Has timestamp", ok: Boolean(finding.observed_at || finding.created_at) },
    { label: "Has analyst summary", ok: Boolean(finding.summary?.trim()) },
    { label: "Has raw output/details", ok: Object.keys(finding.data ?? {}).length > 0 },
    {
      label: "Mentions remediation",
      ok: /remediation|recommend|mitigat|fix|patch/.test(
        `${finding.summary ?? ""} ${blob}`.toLowerCase(),
      ),
    },
    {
      label: "Mentions evidence artifact",
      ok: /screenshot|attachment|evidence|output|banner|nmap|log/.test(
        `${finding.summary ?? ""} ${blob}`.toLowerCase(),
      ),
    },
  ];
}

function extractedIndicators(finding: Finding): string[] {
  const text = JSON.stringify({
    target: finding.target,
    title: finding.title,
    summary: finding.summary,
    data: finding.data,
  });
  const ips = text.match(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g) ?? [];
  const domains = text.match(/\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b/g) ?? [];
  return Array.from(new Set([...ips, ...domains])).slice(0, 30);
}

function scopeIndicators(finding: Finding, scope: ScopeItem[]) {
  const values = extractedIndicators(finding);
  return values.flatMap((value) =>
    scope
      .filter((item) =>
        value.toLowerCase().includes(item.value.toLowerCase()) ||
        item.value.toLowerCase().includes(value.toLowerCase()),
      )
      .map((item) => ({ value, item })),
  );
}

function scopeStateClass(state: string): string {
  if (state === "excluded") return "bg-rose-500/15 text-rose-700 dark:text-rose-200";
  if (state === "declared scope") return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-200";
  if (state === "found scope") return "bg-sky-500/15 text-sky-700 dark:text-sky-200";
  return "bg-amber-500/15 text-amber-700 dark:text-amber-200";
}

function isRelatedFinding(
  finding: Finding,
  other: Finding,
  indicators: string[],
): boolean {
  if (finding.target && other.target && finding.target === other.target) return true;
  if (finding.tool && other.tool && finding.tool === other.tool) return true;
  const tags = new Set(finding.tags ?? []);
  if ((other.tags ?? []).some((tag) => tags.has(tag))) return true;
  const otherText = JSON.stringify({
    target: other.target,
    title: other.title,
    summary: other.summary,
    data: other.data,
  }).toLowerCase();
  return indicators.some((value) => otherText.includes(value.toLowerCase()));
}

function AttachmentsPanel({ finding }: { finding: Finding }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [rows, setRows] = useState<Attachment[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listAttachments(finding.id)
      .then(setRows)
      .catch(() => setRows([]));
  }, [finding.id]);

  async function onFile(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    try {
      const uploaded = await uploadAttachment(finding.id, file);
      setRows((prev) => [...(prev ?? []), uploaded]);
    } finally {
      setBusy(false);
    }
  }

  async function removeAttachment(id: string) {
    if (!window.confirm("Delete this attachment?")) return;
    setBusy(true);
    try {
      await deleteAttachment(id);
      setRows((prev) => (prev ?? []).filter((row) => row.id !== id));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium">Evidence / attachments</h2>
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          className="rounded-md border border-border px-3 py-1 text-xs disabled:opacity-50"
        >
          {busy ? "Uploading…" : "Upload"}
        </button>
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          onChange={(event) => void onFile(event.target.files?.[0])}
        />
      </div>
      {rows === null ? (
        <p className="mt-3 text-xs text-muted-foreground">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">No attachments yet.</p>
      ) : (
        <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {rows.map((row) => (
            <li
              key={row.id}
              className="overflow-hidden rounded-md border border-border bg-background p-2 text-xs"
            >
              <AttachmentPreview attachment={row} />
              <div className="mt-1 flex items-center justify-between gap-2">
                <span className="truncate font-medium">{row.filename}</span>
                <span className="shrink-0 text-muted-foreground">
                  {Math.ceil(row.size_bytes / 1024)} KB
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                <span><DateTime value={row.created_at} /></span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void removeAttachment(row.id)}
                  className="text-destructive hover:underline disabled:opacity-50"
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function AttachmentPreview({ attachment }: { attachment: Attachment }) {
  const [src, setSrc] = useState<string | null>(null);
  const isImage = attachment.content_type.startsWith("image/");
  useEffect(() => {
    if (!isImage) return;
    let objectUrl: string | null = null;
    loadAttachmentBlob(attachment.id)
      .then((url) => {
        objectUrl = url;
        setSrc(url);
      })
      .catch(() => setSrc(null));
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attachment.id, isImage]);
  if (!isImage) {
    return (
      <div className="flex h-16 items-center justify-center rounded bg-muted/40 font-mono text-[10px] text-muted-foreground">
        {attachment.filename.split(".").pop()?.toUpperCase() || "FILE"}
      </div>
    );
  }
  if (!src) {
    return (
      <div className="flex h-24 items-center justify-center rounded bg-muted/40 text-[10px] text-muted-foreground">
        Loading preview…
      </div>
    );
  }
  return (
    <a href={src} target="_blank" rel="noopener noreferrer" title="Open full size">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={attachment.filename}
        className="h-32 w-full rounded object-cover"
      />
    </a>
  );
}

function ContextPromotionPanel({ finding, slug }: { finding: Finding; slug: string }) {
  const { data: candidates, isLoading, error } = useFindingContext(finding.id);
  const promote = usePromoteFindingContextMutation(finding.id, slug);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [manualType, setManualType] = useState("domain");
  const [manualValue, setManualValue] = useState("");
  const [result, setResult] = useState<{
    message: string;
    showEntities: boolean;
    showScope: boolean;
  } | null>(null);
  const keyFor = (type: string, value: string) => `${type}:${value}`;

  function submit(items: Array<{ type: string; value: string; add_to_entities: boolean; add_to_scope: boolean }>) {
    const expandsScope = items.some((item) => item.add_to_scope);
    if (
      expandsScope &&
      !window.confirm(
        "Add the selected values to Found Scope? This makes them eligible for future approval-gated enumeration and scan actions.",
      )
    ) return;
    setResult(null);
    promote.mutate(items, {
      onSuccess: (response) => {
        setSelected(new Set());
        setManualValue("");
        setResult({
          message: `Created ${response.entities_created} entities, ${response.entity_links_created} provenance links, and ${response.scope_items_created} scope items.`,
          showEntities: items.some((item) => item.add_to_entities),
          showScope: items.some((item) => item.add_to_scope),
        });
      },
    });
  }

  const selectedRows = (candidates ?? []).filter((row) =>
    selected.has(keyFor(row.type, row.value)),
  );

  return (
    <section id="discovered-context" className="scroll-mt-6 rounded-lg border border-border bg-card/40 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium">Discovered context</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Promote indicators from this finding into persistent Entities, Found Scope, or both.
          </p>
        </div>
        {selectedRows.length > 0 && (
          <div className="flex flex-wrap gap-2">
            <SmallButton
              disabled={promote.isPending}
              onClick={() => submit(selectedRows.map((row) => ({
                type: row.type,
                value: row.value,
                add_to_entities: true,
                add_to_scope: false,
              })))}
            >
              Add {selectedRows.length} to Entities
            </SmallButton>
            <SmallButton
              disabled={promote.isPending || selectedRows.every((row) => !row.scope_compatible)}
              onClick={() => submit(selectedRows.map((row) => ({
                type: row.type,
                value: row.value,
                add_to_entities: true,
                add_to_scope: row.scope_compatible,
              })))}
            >
              Add eligible to both
            </SmallButton>
          </div>
        )}
      </div>

      {isLoading ? (
        <p className="mt-3 text-xs text-muted-foreground">Extracting candidates…</p>
      ) : error ? (
        <p className="mt-3 text-xs text-destructive">Could not load finding context.</p>
      ) : (candidates ?? []).length === 0 ? (
        <p className="mt-3 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
          No structured indicators were extracted. Add one manually below.
        </p>
      ) : (
        // Grouped by type (subdomain / domain / url / ip / ...) so a
        // finding that yielded 30 subdomains reads as one section, not
        // 30 rows. Each candidate is a compact card with checkbox +
        // saved/scope badges + inline promote buttons.
        <div className="mt-3 max-h-[60vh] space-y-2 overflow-y-auto pr-1">
          {Object.entries(
            (candidates ?? []).reduce<Record<string, typeof candidates>>((acc, row) => {
              (acc[row.type] ||= []).push(row);
              return acc;
            }, {}),
          )
            .sort(([, a], [, b]) => (b?.length ?? 0) - (a?.length ?? 0))
            .map(([type, rows]) => (
              <section
                key={type}
                className="rounded-md border border-border/70 bg-background"
              >
                <header className="flex items-center justify-between gap-2 border-b border-border/60 px-2 py-1.5 text-[11px]">
                  <span className="font-medium">
                    {type}
                    <span className="ml-1 text-[10px] text-muted-foreground">
                      ({rows?.length ?? 0})
                    </span>
                  </span>
                </header>
                <ul className="divide-y divide-border/40">
                  {(rows ?? []).map((row) => {
                    const key = keyFor(row.type, row.value);
                    const savedEntity = !!row.entity_id;
                    const savedScope = !!row.scope_item_id;
                    return (
                      <li
                        key={key}
                        className="flex items-center gap-2 px-2 py-1"
                      >
                        <input
                          type="checkbox"
                          checked={selected.has(key)}
                          onChange={(event) =>
                            setSelected((previous) => {
                              const next = new Set(previous);
                              if (event.target.checked) next.add(key);
                              else next.delete(key);
                              return next;
                            })
                          }
                          className="shrink-0"
                        />
                        {savedEntity ? (
                          <Link
                            href={`/e/entities?slug=${encodeURIComponent(slug)}&type=${encodeURIComponent(row.type)}&value=${encodeURIComponent(row.value)}`}
                            className="min-w-0 flex-1 truncate rounded-sm font-mono text-xs hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            title={`Open entity ${row.value}`}
                          >
                            {row.value}
                          </Link>
                        ) : (
                          <span
                            className="min-w-0 flex-1 truncate font-mono text-xs"
                            title={row.value}
                          >
                            {row.value}
                          </span>
                        )}
                        <span className="flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground">
                          {savedEntity && (
                            <span className="rounded bg-emerald-500/15 px-1 py-0.5 text-emerald-700 dark:text-emerald-300">
                              entity
                            </span>
                          )}
                          {savedScope && (
                            <Link
                              href={`/e?slug=${encodeURIComponent(slug)}&view=scope`}
                              className="rounded bg-sky-500/15 px-1 py-0.5 text-sky-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:text-sky-300"
                              title={`Open Scope for ${row.value}`}
                            >
                              {row.scope_source ?? "scope"}
                            </Link>
                          )}
                          {!savedEntity && (
                            <SmallButton
                              disabled={promote.isPending}
                              onClick={() =>
                                submit([
                                  {
                                    type: row.type,
                                    value: row.value,
                                    add_to_entities: true,
                                    add_to_scope: false,
                                  },
                                ])
                              }
                            >
                              Entity
                            </SmallButton>
                          )}
                          {row.scope_compatible && !savedScope && (
                            <SmallButton
                              disabled={promote.isPending}
                              onClick={() =>
                                submit([
                                  {
                                    type: row.type,
                                    value: row.value,
                                    add_to_entities: false,
                                    add_to_scope: true,
                                  },
                                ])
                              }
                            >
                              Scope
                            </SmallButton>
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <select
          value={manualType}
          onChange={(event) => setManualType(event.target.value)}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs"
        >
          {["domain", "ip", "cidr", "url", "email", "host", "person", "phone"].map((type) => (
            <option key={type} value={type}>{type}</option>
          ))}
        </select>
        <input
          value={manualValue}
          onChange={(event) => setManualValue(event.target.value)}
          placeholder="Add a missed entity value"
          className="h-8 min-w-52 flex-1 rounded-md border border-input bg-background px-2 font-mono text-xs"
        />
        <SmallButton
          disabled={promote.isPending || !manualValue.trim()}
          onClick={() => submit([{
            type: manualType,
            value: manualValue.trim(),
            add_to_entities: true,
            add_to_scope: false,
          }])}
        >
          Add entity
        </SmallButton>
        {new Set(["domain", "ip", "cidr", "url"]).has(manualType) && (
          <SmallButton
            disabled={promote.isPending || !manualValue.trim()}
            onClick={() => submit([{
              type: manualType,
              value: manualValue.trim(),
              add_to_entities: true,
              add_to_scope: true,
            }])}
          >
            Add to both
          </SmallButton>
        )}
      </div>
      {result && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-emerald-700 dark:text-emerald-200">
          <span>{result.message}</span>
          {result.showEntities && (
            <Link href={`/e?slug=${encodeURIComponent(slug)}&view=entities`} className="font-medium underline">
              View Entities
            </Link>
          )}
          {result.showScope && (
            <Link href={`/e?slug=${encodeURIComponent(slug)}&view=scope`} className="font-medium underline">
              View Scope
            </Link>
          )}
        </div>
      )}
      {promote.error && (
        <p className="mt-2 text-xs text-destructive">
          {promote.error instanceof Error ? promote.error.message : "Promotion failed"}
        </p>
      )}
    </section>
  );
}

function ScopeStatusPanel({ finding, slug }: { finding: Finding; slug: string | null }) {
  const [scope, setScope] = useState<ScopeItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    if (!slug) {
      setScope([]);
      setLoadError(null);
      return;
    }
    setLoadError(null);
    listScope(slug)
      .then(setScope)
      .catch(() => {
        setScope([]);
        setLoadError("Couldn’t load engagement scope.");
      });
  }, [slug]);
  const indicators = scopeIndicators(finding, scope ?? []);
  const exclusions = indicators.filter((i) => i.item.is_exclusion);
  const inclusions = indicators.filter((i) => !i.item.is_exclusion);
  const state = exclusions.length
    ? "excluded"
    : inclusions.some((i) => i.item.source === "found")
      ? "found scope"
      : inclusions.length
        ? "declared scope"
        : "unknown";

  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">Scope / ROE status</h2>
        <div className="flex items-center gap-2">
          {slug && (
            <Link href={`/e?slug=${encodeURIComponent(slug)}&view=scope`} className="text-xs text-muted-foreground hover:text-foreground hover:underline">
              Open Scope
            </Link>
          )}
          <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", scopeStateClass(state))}>
            {state}
          </span>
        </div>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Compares extracted IP/domain/URL indicators from this finding against
        engagement scope and exclusions.
      </p>
      {loadError ? (
        <p className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-200">
          {loadError} Confirm ROE manually before approving active agent actions.
        </p>
      ) : scope === null ? (
        <p className="mt-3 text-xs text-muted-foreground">Loading scope…</p>
      ) : indicators.length === 0 ? (
        <p className="mt-3 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
          No exact scope match found for the extracted indicators. Confirm ROE
          before approving active agent actions.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {indicators.map(({ value, item }) => (
            <li key={`${item.id}-${value}`} className="rounded-md border border-border bg-background p-2 text-xs">
              <span className="font-mono">{value}</span>
              <span className="ml-2 text-muted-foreground">
                matched {item.kind}:{item.value} · {item.is_exclusion ? "exclusion" : item.source ?? "defined"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RelatedPanel({ finding, slug }: { finding: Finding; slug: string | null }) {
  const [rows, setRows] = useState<Finding[] | null>(null);
  const { data: contextCandidates } = useFindingContext(finding.id);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    if (!slug) {
      setRows([]);
      setLoadError(null);
      return;
    }
    setLoadError(null);
    listFindings(slug)
      .then(setRows)
      .catch(() => {
        setRows([]);
        setLoadError("Couldn’t load engagement findings.");
      });
  }, [slug]);
  const indicators = extractedIndicators(finding);
  const related = (rows ?? [])
    .filter((row) => row.id !== finding.id)
    .filter((row) => isRelatedFinding(finding, row, indicators))
    .slice(0, 10);

  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <h2 className="text-sm font-medium">Related findings / entities</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Same target, tags, source tool, or extracted IP/domain indicators.
      </p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {indicators.length === 0 ? (
          <span className="text-xs text-muted-foreground">No extracted entities.</span>
        ) : (
          indicators.map((value) => {
            const candidate = (contextCandidates ?? []).find(
              (row) => row.value === value,
            );
            return candidate && slug ? (
              <Link
                key={value}
                href={`/e/entities?slug=${encodeURIComponent(slug)}&type=${encodeURIComponent(candidate.type)}&value=${encodeURIComponent(candidate.value)}`}
                className="rounded-full border border-border bg-muted/40 px-2 py-0.5 font-mono text-[10px] hover:border-foreground/30 hover:bg-muted hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {value}
              </Link>
            ) : (
              <span key={value} className="rounded-full border border-border bg-muted/40 px-2 py-0.5 font-mono text-[10px]">
                {value}
              </span>
            );
          })
        )}
      </div>
      {loadError ? (
        <p className="mt-3 text-xs text-amber-600 dark:text-amber-300">{loadError}</p>
      ) : rows === null ? (
        <p className="mt-3 text-xs text-muted-foreground">Loading…</p>
      ) : related.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">No related findings found.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {related.map((row) => (
            <li key={row.id}>
              <Link
                href={`/e/findings/${row.id}?slug=${encodeURIComponent(slug ?? "")}`}
                className="block rounded-md border border-border bg-background p-2 text-xs hover:border-foreground/30 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <p className="font-medium">{row.title}</p>
                <p className="mt-1 text-muted-foreground">
                  {row.severity} · {row.status} · {row.target ?? "no target"}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ReportPreviewPanel({ finding }: { finding: Finding }) {
  const included = finding.status === "validated" && !finding.exclusion;
  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium">Report preview</h2>
        <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium", included ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-200" : "bg-muted text-muted-foreground")}>
          {included ? "reportable" : "not reportable yet"}
        </span>
      </div>
      <div className="mt-3 rounded-md border border-border bg-background p-4">
        <div className="flex flex-wrap gap-2">
          <Badge variant="outline" className={cn("border", SEVERITY_CLASS[finding.severity])}>
            {finding.severity}
          </Badge>
          <Badge variant="secondary" className="text-[10px]">
            {PHASE_LABEL[finding.phase]}
          </Badge>
        </div>
        <h3 className="mt-3 text-base font-semibold">{finding.title}</h3>
        {finding.target && <p className="mt-1 font-mono text-xs text-muted-foreground">{finding.target}</p>}
        <p className="mt-3 whitespace-pre-wrap text-sm">
          {finding.summary || "No report narrative has been written yet."}
        </p>
      </div>
    </section>
  );
}

function DetailsPanel({ finding, slug }: { finding: Finding; slug: string | null }) {
  const router = useRouter();
  const [deleting, setDeleting] = useState(false);

  async function removeFinding() {
    if (!window.confirm("Delete this finding? It will be hidden from findings, reports, and exports.")) return;
    setDeleting(true);
    try {
      await deleteFinding(finding.id);
      router.replace(slug ? `/e?slug=${encodeURIComponent(slug)}` : "/");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <section className="rounded-lg border border-border bg-card/40 p-4 lg:col-span-2">
        <h2 className="text-sm font-medium">Finding details</h2>
        <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
          <InfoTile label="Tool" value={finding.tool ?? "manual"} />
          <InfoTile label="Target" value={finding.target ?? "—"} />
          <InfoTile label="Created" value={<DateTime value={finding.created_at} />} />
          <InfoTile label="Observed" value={<DateTime value={finding.observed_at} />} />
        </div>
        {/* Items table lives above the tabs (always-visible block) so
            it's not duplicated here. Raw JSON payload still available
            below for legacy / manual inspection. */}
        <details className="mt-4 rounded-md border border-border bg-background">
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium">Raw payload</summary>
          <pre className="max-h-96 overflow-auto border-t border-border p-3 font-mono text-xs text-muted-foreground">
            {JSON.stringify({ args: finding.args, data: finding.data }, null, 2)}
          </pre>
        </details>
      </section>
      <section className="rounded-lg border border-destructive/30 bg-destructive/5 p-4">
        <h2 className="text-sm font-medium text-destructive">Danger zone</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Soft-delete this finding from analyst views and client exports. Audit history is retained.
        </p>
        <button
          type="button"
          disabled={deleting}
          onClick={() => void removeFinding()}
          className="mt-3 rounded-md border border-destructive/40 px-3 py-1.5 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-50"
        >
          {deleting ? "Deleting…" : "Delete finding"}
        </button>
      </section>
    </>
  );
}

function InfoTile({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-background p-2">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 break-words text-xs">{value}</p>
    </div>
  );
}

function openToolActions(messages: FindingChatMessage[]) {
  return messages.flatMap((m) =>
    (m.action_payload?.actions ?? [])
      .map((action, index) => ({ messageId: m.id, action, index }))
      .filter(
        ({ action }) =>
          (action.status ?? "proposed") === "proposed" &&
          action.type === "run_tool",
      ),
  );
}

function AgentToolsPanel({ findingId, slug }: { findingId: string; slug: string | null }) {
  const { data: chat } = useFindingChat(findingId);
  const acceptAction = useAcceptFindingChatActionMutation(findingId);
  const denyAction = useDenyFindingChatActionMutation(findingId);
  const proposedActions = openToolActions(chat?.messages ?? []);
  const [tasks, setTasks] = useState<Task[] | null>(null);

  const refreshTasks = useCallback(() => {
    if (!slug) {
      setTasks([]);
      return;
    }
    listTasks(slug).then(setTasks).catch(() => setTasks([]));
  }, [slug]);

  useEffect(() => {
    refreshTasks();
  }, [refreshTasks, findingId]);

  const findingTasks = (tasks ?? []).filter((task) => task.finding_id === findingId);

  return (
    <section className="space-y-4">
      <div className="rounded-lg border border-amber-400/40 bg-amber-400/10 p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-medium">
          <Sparkles className="h-4 w-4 text-amber-500" />
          Agent tool queue ({proposedActions.length})
        </h2>
        {acceptAction.isPending && (
          <span className="text-[10px] text-muted-foreground">Approving…</span>
        )}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Executable enum/scan tool runs proposed by the AI. Approve one to create
        and dispatch a Tactical task; active tools still stop at the approval
        gate.
      </p>
      {proposedActions.length === 0 ? (
        <p className="mt-3 rounded-md border border-dashed border-amber-500/30 p-3 text-xs text-muted-foreground">
          No executable tool actions yet. Ask the AI tab for “agent actions” to
          generate approval cards.
        </p>
      ) : (
        <div className="mt-3 max-h-[32rem] space-y-2 overflow-y-auto pr-1">
          {proposedActions.map(({ messageId, action, index }) => (
            <ActionCard
              key={`${messageId}-${index}`}
              action={action}
              onAccept={() =>
                acceptAction.mutate(
                  { messageId, actionIndex: index },
                  { onSuccess: refreshTasks },
                )
              }
              accepting={acceptAction.isPending}
              onDeny={() =>
                denyAction.mutate({ messageId, actionIndex: index })
              }
              denying={denyAction.isPending}
              slug={slug}
            />
          ))}
        </div>
      )}
        {acceptAction.error && (
          <p className="mt-3 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
            {acceptAction.error instanceof Error
              ? acceptAction.error.message
              : "Tool dispatch failed"}
          </p>
        )}
      </div>
      <ActionHistoryPanel tasks={findingTasks} loading={tasks === null} slug={slug} />
    </section>
  );
}

function ActionHistoryPanel({
  tasks,
  loading,
  slug,
}: {
  tasks: Task[];
  loading: boolean;
  slug: string | null;
}) {
  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <h2 className="text-sm font-medium">Action history</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Approved agent actions, task status, run ids, and dispatch metadata.
      </p>
      {loading ? (
        <p className="mt-3 text-xs text-muted-foreground">Loading tasks…</p>
      ) : tasks.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">
          No approved tool actions yet.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {tasks.map((task) => (
            <li key={task.id} className="rounded-md border border-border bg-background p-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                {slug ? (
                  <Link
                    href={`/e?slug=${encodeURIComponent(slug)}&view=status&run=${encodeURIComponent(task.id)}`}
                    className="rounded-sm font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {task.title}
                  </Link>
                ) : (
                  <p className="font-medium">{task.title}</p>
                )}
                <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                  {task.status}
                </span>
              </div>
              <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                {String(task.payload.tool ?? "?")} → {String(task.payload.target ?? "?")}
              </p>
              <div className="mt-2 grid grid-cols-1 gap-2 text-[10px] text-muted-foreground sm:grid-cols-3">
                <span>
                  run:{" "}
                  {slug ? (
                    <Link
                      href={`/e?slug=${encodeURIComponent(slug)}&view=status&run=${encodeURIComponent(task.id)}`}
                      className="hover:underline"
                    >
                      {task.run_id ?? "open task"}
                    </Link>
                  ) : (
                    task.run_id ?? "not dispatched"
                  )}
                </span>
                <span>sent: <DateTime value={task.dispatched_at} /></span>
                <span>done: <DateTime value={task.completed_at} /></span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function isCancellableTask(task: Task): boolean {
  return ["pending", "dispatched", "running"].includes(task.status);
}

function ChatRail({
  findingId,
  slug,
}: {
  findingId: string;
  slug: string | null;
}) {
  const [message, setMessage] = useState("");
  const { data: chat, isLoading } = useFindingChat(findingId);
  const ask = useAskFindingChatMutation(findingId);
  const clear = useClearFindingChatMutation(findingId);
  const summarize = useSummarizeFindingChatMutation(findingId);
  const acceptAction = useAcceptFindingChatActionMutation(findingId);
  const denyAction = useDenyFindingChatActionMutation(findingId);
  const { data: findings } = useFindings(slug ?? "");
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const messages = chat?.messages ?? [];
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to the latest message whenever the conversation grows or
  // the "Thinking…" indicator appears. Keeps new AI responses in view
  // without the analyst reaching for the scroll bar.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, ask.isPending]);

  // Live run status for accepted run_tool action bubbles: resolves the
  // dispatched Task + counts findings its run produced, so the chat shows
  // the outcome without navigating to Status/Findings.
  const runOutcome = (action: FindingChatAction): string | null => {
    if (action.type !== "run_tool" || action.status !== "accepted") return null;
    const r = (action.result ?? {}) as Record<string, unknown>;
    const taskId = typeof r.task_id === "string" ? r.task_id : null;
    const runId = typeof r.run_id === "string" ? r.run_id : null;
    const task = (tasks ?? []).find((t) => t.id === taskId);
    if (!task) {
      return r.dispatched ? "dispatched" : null;
    }
    let s = `run ${task.status}`;
    if (task.status === "completed" && runId) {
      const n = (findings ?? []).filter(
        (f) => (f.thread_id ?? null) === runId,
      ).length;
      s += ` · ${n} finding${n === 1 ? "" : "s"}`;
    } else if (task.status === "failed") {
      s += " · likely out of scope or blocked at approval";
    } else if (task.status === "running" || task.status === "dispatched") {
      s += "…";
    }
    return s;
  };

  useEffect(() => {
    if (!slug) {
      setTasks([]);
      return;
    }
    listTasks(slug)
      .then(setTasks)
      .catch(() => setTasks([]));
    const id = window.setInterval(() => {
      listTasks(slug)
        .then(setTasks)
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = message.trim();
    if (!text || ask.isPending) return;
    setMessage("");
    ask.mutate(
      { message: text, conversation_id: chat?.conversation_id ?? null },
      {
        onError: () => setMessage(text),
      },
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-medium">
            <MessageSquare className="h-4 w-4 text-muted-foreground" />
            AI conversation
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Concise finding context and planning. Executable actions appear in
            the Tools tab.
          </p>
        </div>
        <button
          type="button"
          onClick={() => summarize.mutate()}
          disabled={summarize.isPending || clear.isPending || messages.length === 0}
          className="rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          title="Summarize this conversation into the activity log, then clear it"
        >
          {summarize.isPending ? "Summarizing…" : "Summarize & close"}
        </button>
      </div>

      <div ref={scrollRef} className="mt-4 max-h-[30rem] space-y-3 overflow-y-auto pr-1">
        {isLoading ? (
          <p className="text-xs text-muted-foreground">Loading chat…</p>
        ) : messages.length === 0 ? (
          <div className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
            Start fresh. Ask “suggest agent actions” to populate the Tools tab,
            or ask for a concise summary of gaps.
          </div>
        ) : (
          messages.map((m) => (
            <ChatBubble
              key={m.id}
              message={m}
              onAccept={(messageId, actionIndex) =>
                acceptAction.mutate({ messageId, actionIndex })
              }
              onDeny={(messageId, actionIndex) =>
                denyAction.mutate({ messageId, actionIndex })
              }
              accepting={acceptAction.isPending}
              denying={denyAction.isPending}
              runOutcome={runOutcome}
              slug={slug}
            />
          ))
        )}
        {ask.isPending && (
          <div className="rounded-md bg-muted/60 p-3 text-xs text-muted-foreground">
            Thinking over the finding dossier…
          </div>
        )}
      </div>

      {(ask.error || clear.error) && (
        <ChatErrorNotice error={ask.error ?? clear.error} />
      )}

      <form onSubmit={onSubmit} className="mt-4 space-y-2">
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Ask about this finding…"
          className="min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none ring-offset-background placeholder:text-muted-foreground focus:ring-2 focus:ring-ring focus:ring-offset-2"
          disabled={ask.isPending || clear.isPending}
          maxLength={4000}
        />
        <button
          type="submit"
          disabled={!message.trim() || ask.isPending || clear.isPending}
          className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >
          {ask.isPending ? "Asking…" : "Ask AI"}
        </button>
      </form>
    </div>
  );
}

function ChatErrorNotice({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.code === "missing_provider_key") {
    return (
      <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-200">
        <p className="font-medium">Provider key needed</p>
        <p className="mt-1">{error.message.replace(/^\d+\s+[^:]+:\s*/, "")}</p>
        {error.actionUrl && (
          <a
            href={error.actionUrl}
            className="mt-2 inline-flex rounded border border-amber-500/40 px-2 py-1 font-medium hover:bg-amber-500/10"
          >
            {error.actionLabel ?? "Open settings"}
          </a>
        )}
      </div>
    );
  }
  return (
    <p className="mt-3 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
      {error instanceof Error ? error.message : "Chat failed"}
    </p>
  );
}

function ChatBubble({
  message,
  onAccept,
  onDeny,
  accepting,
  denying,
  runOutcome,
  slug,
}: {
  message: FindingChatMessage;
  onAccept: (messageId: string, actionIndex: number) => void;
  onDeny: (messageId: string, actionIndex: number) => void;
  accepting: boolean;
  denying: boolean;
  runOutcome: (action: FindingChatAction) => string | null;
  slug: string | null;
}) {
  const mine = message.role === "user";
  const actions =
    (message.action_payload?.actions ?? []).filter(
      (a) => a.type !== "context",
    );
  return (
    <div
      className={cn(
        "rounded-lg border p-3 text-sm",
        mine
          ? "ml-6 border-primary/30 bg-primary/10"
          : "mr-6 border-border bg-background",
      )}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {mine ? "Analyst" : "Assistant"}
        </span>
        <span className="text-[10px] text-muted-foreground">
          <DateTime value={message.created_at} />
        </span>
      </div>
      <p className="whitespace-pre-wrap leading-relaxed">{message.content}</p>
      {actions.length > 0 && (
        <div className="mt-2 space-y-2">
          {actions.map((action, index) => (
            <ActionCard
              key={`${message.id}-${index}`}
              action={action}
              onAccept={() => onAccept(message.id, index)}
              accepting={accepting}
              onDeny={() => onDeny(message.id, index)}
              denying={denying}
              runOutcome={runOutcome(action)}
              slug={slug}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ActionCard({
  action,
  onAccept,
  accepting,
  onDeny,
  denying,
  runOutcome,
  slug,
}: {
  action: FindingChatAction;
  onAccept: () => void;
  accepting: boolean;
  onDeny: () => void;
  denying: boolean;
  runOutcome?: string | null;
  slug: string | null;
}) {
  const accepted = action.status === "accepted";
  const denied = action.status === "denied";
  const isContext = action.type === "context";
  const taskId = typeof action.result?.task_id === "string" ? action.result.task_id : null;
  const resultFindingId = typeof action.result?.finding_id === "string" ? action.result.finding_id : null;
  const resultHref = slug && taskId
    ? `/e?slug=${encodeURIComponent(slug)}&view=status&run=${encodeURIComponent(taskId)}`
    : slug && resultFindingId
      ? `/e/findings/${resultFindingId}?slug=${encodeURIComponent(slug)}`
      : null;
  const resultLabel = runOutcome ?? (action.result ? summarizeResult(action.result) : null);
  return (
    <div className="rounded-md border border-amber-400/30 bg-amber-400/10 p-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-foreground">
            {actionLabel(action.type)} · {action.title}
          </div>
          {action.description && (
            <p className="mt-1 text-xs text-muted-foreground">
              {action.description}
            </p>
          )}
          {accepted && (
            <p className="mt-1 text-[10px] text-emerald-600 dark:text-emerald-300">
              Approved
              {resultLabel && (resultHref ? (
                <>
                  {runOutcome ? " · " : ": "}
                  <Link href={resultHref} className="font-medium underline">
                    {resultLabel}
                  </Link>
                </>
              ) : runOutcome ? ` · ${resultLabel}` : `: ${resultLabel}`)}
            </p>
          )}
          {denied && (
            <p className="mt-1 text-[10px] text-muted-foreground">
              Declined
            </p>
          )}
        </div>
        {!accepted && !denied && !isContext && (
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={onAccept}
              disabled={accepting || denying}
              className="rounded border border-amber-500/40 px-2 py-1 text-[11px] font-medium hover:bg-amber-500/10 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Approve
            </button>
            <button
              type="button"
              onClick={onDeny}
              disabled={accepting || denying}
              className="rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
              title="Decline this action so the assistant won't re-suggest it"
            >
              Deny
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function actionLabel(type: FindingChatAction["type"]): string {
  switch (type) {
    case "next_step":
      return "Next step";
    case "tag_incident":
      return "Tag";
    case "add_finding":
      return "Add finding";
    case "run_tool":
      return "Run tool";
    case "context":
      return "Context";
  }
}

function summarizeResult(result: Record<string, unknown>): string {
  if (Array.isArray(result.tags)) return `tags: ${result.tags.join(", ")}`;
  if (typeof result.finding_id === "string") return `finding ${result.finding_id}`;
  if (typeof result.suggestion_id === "string") {
    return `suggestion ${result.suggestion_id}`;
  }
  return "done";
}

function TimelineRow({ entry }: { entry: FindingActivityEntry }) {
  const meta = KIND_META[entry.kind] ?? {
    icon: Activity,
    tint: "text-muted-foreground",
  };
  const Icon = meta.icon;
  const [expanded, setExpanded] = useState(false);
  const isSummary = entry.kind === "finding.chat_summarized";
  const detail = entry.detail ?? "";
  const long = detail.length > 140;
  const shown = long && !expanded ? `${detail.slice(0, 140)}…` : detail;
  return (
    <li className="relative">
      <span
        className={cn(
          "absolute -left-[1.4rem] flex h-5 w-5 items-center justify-center rounded-full bg-card",
        )}
      >
        <Icon className={cn("h-3.5 w-3.5", meta.tint)} />
      </span>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-sm font-medium text-foreground">
          {entry.label}
        </span>
        <span className="text-[11px] text-muted-foreground">
          <DateTime value={entry.ts} />
        </span>
      </div>
      {shown && (
        <p
          className={cn(
            "mt-0.5 whitespace-pre-wrap text-xs text-muted-foreground",
            isSummary && long && "cursor-pointer hover:text-foreground",
          )}
          onClick={isSummary && long ? () => setExpanded((v) => !v) : undefined}
        >
          {shown}
          {isSummary && long && (
            <span className="ml-1 text-[10px] text-primary">
              {expanded ? "(less)" : "(more)"}
            </span>
          )}
        </p>
      )}
      {entry.actor && (
        <p className="text-[10px] uppercase tracking-wide text-muted-foreground/70">
          by {entry.actor}
        </p>
      )}
    </li>
  );
}

export function FindingPaneWithSlug({ id }: { id: string }) {
  const params = useSearchParams();
  const slug = params.get("slug");
  return <FindingPane id={id} slug={slug} />;
}
