"use client";

import Link from "next/link";
import { Suspense, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";
import { ASCII_CAT_PLAYING, PlaceholderPage } from "@/components/placeholder-page";
import { PlaybooksTab } from "@/components/playbooks/playbooks-tab";
import { ReportBuilder } from "@/components/report-builder";
import { useEngagements, useRunningTasks } from "@/lib/hooks";
import type { RunningTask } from "@/lib/api";

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
  const engagements = useMemo(
    () =>
      (engagementsQuery.data ?? []).filter((e) => e.status !== "flushed"),
    [engagementsQuery.data],
  );
  const selected =
    engagements.find((e) => e.slug === requestedSlug) ?? null;

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
              {engagement.name} · {engagement.slug}
              {engagement.status !== "active"
                ? ` (${engagement.status})`
                : ""}
            </option>
          ))}
        </select>
      </label>
      {selected ? (
        <PlaybooksTab engagementSlug={selected.slug} />
      ) : (
        <p className="rounded-lg border border-dashed border-border bg-card/20 p-6 text-sm text-muted-foreground">
          {engagementsQuery.isLoading
            ? "Loading engagements…"
            : engagements.length === 0
              ? "No engagements are available yet."
              : "Select an engagement to view + kick playbook runs."}
        </p>
      )}
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
  const { data, isLoading, error } = useRunningTasks();
  const rows: RunningTask[] = data ?? [];

  if (isLoading) return null;
  if (error) {
    return (
      <section className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-xs text-destructive">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-destructive" />
          <span className="font-medium">Running jobs</span>
          <span>· status unknown — {error instanceof Error ? error.message : String(error)}</span>
        </div>
      </section>
    );
  }
  if (rows.length === 0) {
    return (
      <section className="rounded-lg border border-border bg-card/30 p-4 text-xs text-muted-foreground">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-muted-foreground/60" />
          <span className="font-medium">Running jobs</span><span>· none active</span>
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex items-center gap-2 text-xs">
        <span className="h-2 w-2 rounded-full bg-emerald-500" />
        <span className="font-medium">Running jobs</span>
        <span className="text-muted-foreground">· {rows.length} active</span>
      </div>
      <ul className="mt-3 space-y-3">{rows.map((task) => <RunningJobRow key={task.id} task={task} />)}</ul>
    </section>
  );
}

function RunningJobRow({ task }: { task: RunningTask }) {
  const percent = task.status === "running" ? 66 : task.status === "dispatched" ? 25 : 10;
  const href = `/e?slug=${encodeURIComponent(task.engagement_slug)}&view=status&run=${encodeURIComponent(task.id)}`;
  return (
    <li>
      <Link href={href} className="-mx-2 block rounded-md px-2 py-1.5 hover:bg-secondary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <div className="flex items-center justify-between gap-2 text-xs">
          <div className="min-w-0 flex-1 truncate"><span className="font-medium">{task.title}</span><span className="ml-2 text-muted-foreground">· {task.engagement_slug}</span></div>
          <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{task.status}</span>
        </div>
        <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-secondary/60">
          <div className="h-full rounded-full bg-critical transition-[width] duration-500" style={{ width: `${percent}%` }} />
        </div>
      </Link>
    </li>
  );
}
