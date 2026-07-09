"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
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
import { Badge } from "@/components/ui/badge";
import {
  useAskFindingChatMutation,
  useFinding,
  useFindingActivity,
  useFindingChat,
} from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type {
  FindingActivityEntry,
  FindingChatMessage,
  FindingPhase,
  FindingValidationStatus,
  Severity,
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

function fmtTs(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}

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
          <span>created {fmtTs(finding.created_at)}</span>
          {finding.observed_at && (
            <span>observed {fmtTs(finding.observed_at)}</span>
          )}
        </div>
        {finding.summary && (
          <p className="mt-3 text-sm text-foreground">{finding.summary}</p>
        )}
      </div>

      {/* two-column body */}
      <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* left: activity timeline */}
        <div className="lg:col-span-2">
          <div className="rounded-lg border border-border bg-card/40 p-4">
            <h2 className="mb-3 flex items-center gap-2 text-sm font-medium">
              <Activity className="h-4 w-4 text-muted-foreground" />
              Activity
              <span className="text-xs text-muted-foreground">
                ({entries.length})
              </span>
            </h2>
            {entries.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Nothing recorded yet. Run a tool, triage, or validate to
                populate this timeline.
              </p>
            ) : (
              <ol className="space-y-3 border-l border-border pl-4">
                {entries.map((e, i) => (
                  <TimelineRow key={`${e.ts}-${i}`} entry={e} />
                ))}
              </ol>
            )}
          </div>
        </div>

        {/* right rail: finding-scoped chatbot (Phase 2) */}
        <div>
          <ChatRail findingId={id} />
        </div>
      </div>
    </div>
  );
}

function ChatRail({ findingId }: { findingId: string }) {
  const [message, setMessage] = useState("");
  const { data: chat, isLoading } = useFindingChat(findingId);
  const ask = useAskFindingChatMutation(findingId);
  const messages = chat?.messages ?? [];

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
    <div className="sticky top-6 rounded-lg border border-border bg-card/40 p-4">
      <h2 className="flex items-center gap-2 text-sm font-medium">
        <Sparkles className="h-4 w-4 text-amber-500" />
        AI assistant
      </h2>
      <p className="mt-2 text-xs text-muted-foreground">
        Ask for context or next steps. Phase 2 is read-only: the assistant can
        recommend actions, but nothing runs until future approval bubbles land.
      </p>

      <div className="mt-4 max-h-[30rem] space-y-3 overflow-y-auto pr-1">
        {isLoading ? (
          <p className="text-xs text-muted-foreground">Loading chat…</p>
        ) : messages.length === 0 ? (
          <div className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
            Try “what should I do next?” or “summarize the evidence and gaps.”
          </div>
        ) : (
          messages.map((m) => <ChatBubble key={m.id} message={m} />)
        )}
        {ask.isPending && (
          <div className="rounded-md bg-muted/60 p-3 text-xs text-muted-foreground">
            Thinking over the finding dossier…
          </div>
        )}
      </div>

      {ask.error && (
        <p className="mt-3 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
          {ask.error instanceof Error ? ask.error.message : "Chat failed"}
        </p>
      )}

      <form onSubmit={onSubmit} className="mt-4 space-y-2">
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Ask about this finding…"
          className="min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none ring-offset-background placeholder:text-muted-foreground focus:ring-2 focus:ring-ring focus:ring-offset-2"
          disabled={ask.isPending}
          maxLength={4000}
        />
        <button
          type="submit"
          disabled={!message.trim() || ask.isPending}
          className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >
          {ask.isPending ? "Asking…" : "Ask AI"}
        </button>
      </form>
    </div>
  );
}

function ChatBubble({ message }: { message: FindingChatMessage }) {
  const mine = message.role === "user";
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
          {fmtTs(message.created_at)}
        </span>
      </div>
      <p className="whitespace-pre-wrap leading-relaxed">{message.content}</p>
    </div>
  );
}

function TimelineRow({ entry }: { entry: FindingActivityEntry }) {
  const meta = KIND_META[entry.kind] ?? {
    icon: Activity,
    tint: "text-muted-foreground",
  };
  const Icon = meta.icon;
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
          {fmtTs(entry.ts)}
        </span>
      </div>
      {entry.detail && (
        <p className="mt-0.5 text-xs text-muted-foreground">{entry.detail}</p>
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
