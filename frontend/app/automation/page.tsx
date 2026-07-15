"use client";

// v2.4.0 — Automation shell. Top-level tabs (Recon / Scanning /
// Exploitation / Reporting) mirror the workflow phases; the Reporting
// tab is the only one with real content today — the others show the
// shared "Almost There" placeholder cat. Report generation moved off
// the engagement workbench so an analyst can pick which engagement to
// build a PDF for without navigating in and out. The Running jobs
// banner lives at the bottom of every sub-tab and surfaces
// pending/dispatched/running Tasks across every engagement.
//
// v2.5.0 — Running jobs rows are now hyperlinks that jump straight to
// the referenced task in the engagement's Status view.

import Link from "next/link";
import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import {
  ASCII_CAT_PLAYING,
  PlaceholderPage,
} from "@/components/placeholder-page";
import { ReportBuilder } from "@/components/report-builder";
import { useEngagements, useRunningTasks } from "@/lib/hooks";
import type { RunningTask } from "@/lib/api";

type AutomationTab = "recon" | "scanning" | "exploitation" | "reporting";

const TABS: { id: AutomationTab; label: string }[] = [
  { id: "recon", label: "Recon" },
  { id: "scanning", label: "Scanning" },
  { id: "exploitation", label: "Exploitation" },
  { id: "reporting", label: "Reporting" },
];

export default function AutomationPage() {
  const [tab, setTab] = useState<AutomationTab>("reporting");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Automation</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Pick a workflow to run — automations are managed by admins.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-1 border-b border-border">
        {TABS.map((t) => {
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={cn(
                "-mb-px rounded-t-md border-b-2 px-4 py-2 text-sm transition-colors",
                active
                  ? "border-critical text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
              aria-current={active ? "page" : undefined}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === "reporting" ? <ReportingTab /> : <ComingSoonTab />}

      <RunningJobsBanner />
    </div>
  );
}

function ComingSoonTab() {
  return (
    <PlaceholderPage
      title=""
      tagline="Almost There ......"
      detail="This workflow lands in a later release. The Reporting tab is live today."
      art={ASCII_CAT_PLAYING}
    />
  );
}

function ReportingTab() {
  const engagementsQuery = useEngagements();
  const engagements = useMemo(
    () => (engagementsQuery.data ?? []).filter((e) => e.status !== "flushed"),
    [engagementsQuery.data],
  );
  const [slug, setSlug] = useState<string>("");
  const effectiveSlug =
    slug || (engagements.length > 0 ? engagements[0].slug : "");

  return (
    <div className="space-y-4">
      <label className="flex max-w-xl flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Engagement</span>
        <select
          value={effectiveSlug}
          onChange={(event) => setSlug(event.target.value)}
          disabled={engagementsQuery.isLoading || engagements.length === 0}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        >
          {engagements.length === 0 && (
            <option value="">No engagements available</option>
          )}
          {engagements.map((eng) => (
            <option key={eng.slug} value={eng.slug}>
              {eng.name} · {eng.slug}
              {eng.status !== "active" ? ` (${eng.status})` : ""}
            </option>
          ))}
        </select>
      </label>

      {effectiveSlug ? (
        <ReportBuilder slug={effectiveSlug} />
      ) : (
        <p className="rounded-lg border border-dashed border-border bg-card/20 p-6 text-sm text-muted-foreground">
          No engagements available to report on yet.
        </p>
      )}
    </div>
  );
}

function RunningJobsBanner() {
  const { data, isLoading } = useRunningTasks();
  const rows: RunningTask[] = data ?? [];

  if (isLoading) {
    return null;
  }
  if (rows.length === 0) {
    return (
      <section className="rounded-lg border border-border bg-card/30 p-4 text-xs text-muted-foreground">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-muted-foreground/60" />
          <span className="font-medium">Running jobs</span>
          <span>· none active</span>
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex items-center gap-2 text-xs">
        <span className="h-2 w-2 rounded-full bg-emerald-500" />
        <span className="font-medium">Running jobs</span>
        <span className="text-muted-foreground">
          · {rows.length} active
        </span>
      </div>
      <ul className="mt-3 space-y-3">
        {rows.map((task) => (
          <RunningJobRow key={task.id} task={task} />
        ))}
      </ul>
    </section>
  );
}

function RunningJobRow({ task }: { task: RunningTask }) {
  // Rough progress cue: dispatched but not started = 25%, running = 66%,
  // pending (queued but not yet dispatched) = 10%. We don't have a real
  // percent-complete signal on the backend, so this is intentionally coarse
  // — it just signals "something is happening" rather than pretending to
  // measure work done.
  const percent =
    task.status === "running"
      ? 66
      : task.status === "dispatched"
        ? 25
        : 10;
  // v2.5.0: clicking the row jumps into the engagement's Status view
  // scrolled to the referenced task. Uses `run=<task_id>` — same query
  // shape the finding-pane task history uses so the Status view
  // handles it identically.
  const href = `/e?slug=${encodeURIComponent(task.engagement_slug)}&view=status&run=${encodeURIComponent(task.id)}`;
  return (
    <li>
      <Link
        href={href}
        className="block rounded-md px-2 py-1.5 -mx-2 hover:bg-secondary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex items-center justify-between gap-2 text-xs">
          <div className="min-w-0 flex-1 truncate">
            <span className="font-medium">{task.title}</span>
            <span className="ml-2 text-muted-foreground">
              · {task.engagement_slug}
            </span>
          </div>
          <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
            {task.status}
          </span>
        </div>
        <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-secondary/60">
          <div
            className="h-full rounded-full bg-critical transition-[width] duration-500"
            style={{ width: `${percent}%` }}
          />
        </div>
      </Link>
    </li>
  );
}

