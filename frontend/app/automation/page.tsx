"use client";

import Link from "next/link";
import { Suspense, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";
import { ASCII_CAT_PLAYING, PlaceholderPage } from "@/components/placeholder-page";
import { Plus } from "lucide-react";
import { PlaybooksTab } from "@/components/playbooks/playbooks-tab";
import { PlaybookEditorModal } from "@/components/playbooks/playbook-editor-modal";
import { Button } from "@/components/ui/button";
import { QueryState } from "@/components/query-state";
import { ReportBuilder } from "@/components/report-builder";
import { useEngagements, useMe, useRunningJobs } from "@/lib/hooks";
import type { RunningJob } from "@/lib/api";

type AutomationTab =
  | "playbooks"
  | "recon"
  | "scanning"
  | "exploitation"
  | "reporting";

const TABS: { id: AutomationTab; label: string }[] = [
  { id: "playbooks", label: "Playbooks" },
  { id: "recon", label: "Recon" },
  { id: "scanning", label: "Scanning" },
  { id: "exploitation", label: "Exploitation" },
  { id: "reporting", label: "Reporting" },
];
const VALID_TABS = new Set<AutomationTab>(TABS.map((tab) => tab.id));

export default function AutomationPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading Automation…</p>}>
      <AutomationContent />
    </Suspense>
  );
}

function AutomationContent() {
  const router = useRouter();
  const params = useSearchParams();
  const tabParam = params.get("tab");
  const tab: AutomationTab = tabParam && VALID_TABS.has(tabParam as AutomationTab)
    ? (tabParam as AutomationTab)
    : "reporting";
  const requestedSlug = params.get("slug") ?? "";

  const updateContext = (next: { tab?: AutomationTab; slug?: string }) => {
    const query = new URLSearchParams(params.toString());
    query.set("tab", next.tab ?? tab);
    const slug = next.slug ?? requestedSlug;
    if (slug) query.set("slug", slug);
    else query.delete("slug");
    router.replace(`/automation?${query.toString()}`, { scroll: false });
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Automation</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Pick a workflow to run — automations are managed by admins.
        </p>
      </div>

      <RunningJobsBanner />

      <div className="flex flex-wrap items-center gap-1 border-b border-border">
        {TABS.map((item) => {
          const active = tab === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => updateContext({ tab: item.id })}
              className={cn(
                "-mb-px rounded-t-md border-b-2 px-4 py-2 text-sm transition-colors",
                active ? "border-critical text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
              )}
              aria-current={active ? "page" : undefined}
            >
              {item.label}
            </button>
          );
        })}
      </div>

      {tab === "reporting" ? (
        <ReportingTab requestedSlug={requestedSlug} onSlugChange={(slug) => updateContext({ slug })} />
      ) : tab === "playbooks" ? (
        <PlaybooksEngagementPicker
          requestedSlug={requestedSlug}
          onSlugChange={(slug) => updateContext({ slug })}
        />
      ) : (
        <ComingSoonTab />
      )}
    </div>
  );
}

function PlaybooksEngagementPicker({
  requestedSlug,
  onSlugChange,
}: {
  requestedSlug: string;
  onSlugChange: (slug: string) => void;
}) {
  const engagementsQuery = useEngagements();
  const meQuery = useMe();
  const canWrite = meQuery.data !== undefined && meQuery.data.role !== "guest";
  const [creatingPlaybook, setCreatingPlaybook] = useState(false);
  const engagements = useMemo(
    () =>
      (engagementsQuery.data ?? []).filter((e) => e.status !== "flushed"),
    [engagementsQuery.data],
  );
  const selected =
    engagements.find((e) => e.slug === requestedSlug) ?? null;
  const catalogHeader = (
    <section className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border bg-card/30 p-4">
      <div>
        <h2 className="text-sm font-semibold">Shared playbook catalog</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Recipes are available across engagements. Choose an engagement below only when planning or running one.
        </p>
      </div>
      {canWrite ? (
        <Button type="button" size="sm" onClick={() => setCreatingPlaybook(true)}>
          <Plus className="mr-1.5 h-3.5 w-3.5" /> Add a playbook
        </Button>
      ) : null}
    </section>
  );

  if (
    engagementsQuery.data === undefined &&
    (engagementsQuery.isLoading || engagementsQuery.error)
  ) {
    return (
      <div className="space-y-4">
        {catalogHeader}
        <QueryState
          isLoading={engagementsQuery.isLoading}
          error={engagementsQuery.error}
          loadingLabel="Loading engagement run contexts…"
          errorLabel="Could not load engagement run contexts."
          onRetry={() => void engagementsQuery.refetch()}
          isRetrying={engagementsQuery.isFetching}
        />
        {creatingPlaybook ? (
          <PlaybookEditorModal
            playbook={null}
            onClose={() => setCreatingPlaybook(false)}
          />
        ) : null}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <QueryState
        isLoading={false}
        error={engagementsQuery.error}
        hasData
        compact
        onRetry={() => void engagementsQuery.refetch()}
        isRetrying={engagementsQuery.isFetching}
      />
      {catalogHeader}
      <label className="flex max-w-xl flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Run in engagement</span>
        <select
          value={selected?.slug ?? ""}
          onChange={(event) => onSlugChange(event.target.value)}
          disabled={engagementsQuery.isLoading || engagements.length === 0}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        >
          <option value="">Select an engagement…</option>
          {engagements.map((engagement) => (
            <option key={engagement.slug} value={engagement.slug}>
              {engagement.name} · {engagement.slug}
              {engagement.status !== "active" ? ` (${engagement.status})` : ""}
            </option>
          ))}
        </select>
      </label>
      {selected ? (
        <PlaybooksTab engagementSlug={selected.slug} showCreateAction={false} />
      ) : (
        <p className="rounded-lg border border-dashed border-border bg-card/20 p-6 text-sm text-muted-foreground">
          {engagementsQuery.isLoading
            ? "Loading engagements…"
            : engagements.length === 0
              ? "No engagements are available yet."
              : "Select an engagement to view + kick playbook runs."}
        </p>
      )}
      {creatingPlaybook ? (
        <PlaybookEditorModal
          playbook={null}
          onClose={() => setCreatingPlaybook(false)}
        />
      ) : null}
    </div>
  );
}

function ComingSoonTab() {
  return <PlaceholderPage title="" tagline="Almost There ......" detail="This workflow lands in a later release. The Reporting tab is live today." art={ASCII_CAT_PLAYING} />;
}

function ReportingTab({ requestedSlug, onSlugChange }: { requestedSlug: string; onSlugChange: (slug: string) => void }) {
  const engagementsQuery = useEngagements();
  const engagements = useMemo(
    () => (engagementsQuery.data ?? []).filter((engagement) => engagement.status !== "flushed"),
    [engagementsQuery.data],
  );
  const selected = engagements.find((engagement) => engagement.slug === requestedSlug) ?? null;
  const queryError = engagementsQuery.error;

  if (queryError) {
    return (
      <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
        Engagements could not be loaded; report context is unknown. {queryError instanceof Error ? queryError.message : String(queryError)}
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <label className="flex max-w-xl flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Engagement</span>
        <select
          value={selected?.slug ?? ""}
          onChange={(event) => onSlugChange(event.target.value)}
          disabled={engagementsQuery.isLoading || engagements.length === 0}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        >
          <option value="">Select an engagement…</option>
          {engagements.map((engagement) => (
            <option key={engagement.slug} value={engagement.slug}>
              {engagement.name} · {engagement.slug}{engagement.status !== "active" ? ` (${engagement.status})` : ""}
            </option>
          ))}
        </select>
      </label>

      {requestedSlug && !engagementsQuery.isLoading && !selected && (
        <p role="alert" className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-100">
          The requested engagement “{requestedSlug}” is unavailable. Select another engagement explicitly.
        </p>
      )}

      {selected ? (
        <ReportBuilder slug={selected.slug} />
      ) : (
        <p className="rounded-lg border border-dashed border-border bg-card/20 p-6 text-sm text-muted-foreground">
          {engagementsQuery.isLoading
            ? "Loading engagements…"
            : engagements.length === 0
              ? "No engagements are available to report on yet."
              : "Select an engagement to build its report."}
        </p>
      )}
    </div>
  );
}

function RunningJobsBanner() {
  const query = useRunningJobs();
  const rows: RunningJob[] = query.data ?? [];

  if (query.data === undefined && (query.isLoading || query.error)) {
    return (
      <section className="rounded-lg border border-border bg-card/30 p-4">
        <QueryState
          isLoading={query.isLoading}
          error={query.error}
          loadingLabel="Loading running jobs…"
          errorLabel="Could not load running jobs."
          onRetry={() => void query.refetch()}
          isRetrying={query.isFetching}
          compact
        />
      </section>
    );
  }
  if (rows.length === 0) {
    return (
      <section className="rounded-lg border border-border bg-card/30 p-4 text-xs text-muted-foreground">
        <QueryState
          isLoading={false}
          error={query.error}
          hasData
          onRetry={() => void query.refetch()}
          isRetrying={query.isFetching}
          compact
        />
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-muted-foreground/60" />
          <span className="font-medium">Running jobs</span><span>· none active</span>
        </div>
      </section>
    );
  }

  return (
    <section className="space-y-3 rounded-lg border border-border bg-card/40 p-4">
      <QueryState
        isLoading={false}
        error={query.error}
        hasData
        onRetry={() => void query.refetch()}
        isRetrying={query.isFetching}
        compact
      />
      <div className="flex items-center gap-2 text-xs">
        <span className="h-2 w-2 rounded-full bg-emerald-500" />
        <span className="font-medium">Running jobs</span>
        <span className="text-muted-foreground">· {rows.length} active</span>
      </div>
      <ul className="space-y-3">
        {rows.map((task) => <RunningJobRow key={`${task.kind}-${task.id}`} task={task} />)}
      </ul>
    </section>
  );
}

function RunningJobRow({ task }: { task: RunningJob }) {
  const href = `/e?slug=${encodeURIComponent(task.engagement_slug)}&view=status&run=${encodeURIComponent(task.id)}`;
  const progress =
    task.kind === "playbook" && task.steps_total !== null && task.steps_total > 0
      ? `${task.steps_completed ?? 0}/${task.steps_total} steps`
      : null;
  return (
    <li>
      <Link href={href} className="-mx-2 block rounded-md px-2 py-1.5 hover:bg-secondary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <div className="flex items-center justify-between gap-2 text-xs">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span
              className={cn(
                "h-2 w-2 shrink-0 rounded-full",
                task.awaiting_action ? "bg-amber-500" : "bg-emerald-500",
              )}
            />
            <span className="truncate font-medium">{task.title}</span>
            <span className="truncate text-muted-foreground">· {task.engagement_slug}</span>
          </div>
          <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
            {task.awaiting_action ? "needs approval" : task.status}
            {progress ? ` · ${progress}` : ""}
          </span>
        </div>
        <p className="ml-4 mt-1 text-[10px] text-muted-foreground">
          {task.kind === "playbook" ? "Playbook run" : "Legacy task"}
        </p>
      </Link>
    </li>
  );
}
