"use client";

// v2.0.0: this WAS app/page.tsx (the landing engagement chooser).
// Now it lives at /engagements — the primary route the LeftSidebar
// links to. The old app/page.tsx is a server-side redirect to here.
//
// v2.4.0: pending as a derived state + the four stat tiles are now
// filter toggles (same UX as the severity tiles on Findings). Default
// filter shows Pending + Active side by side and hides Archived; the
// analyst clicks Archived to inspect archived engagements. Clicking any
// tile a second time reverts to the default view.

import Link from "next/link";
import { useState } from "react";
import { Archive, Clock, Crosshair, Layers, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { EngagementCard } from "@/components/engagement-card";
import { useEngagements } from "@/lib/hooks";
import { isPendingEngagement, pendingReason } from "@/lib/engagement-status";
import type { Engagement } from "@/lib/types";

// Filter state matches the severity-tile pattern on Findings: single
// select where clicking an already-selected tile falls back to "default"
// (workspace view — Active + Pending, no Archived).
type EngagementFilter = "default" | "active" | "pending" | "archived";

export default function EngagementListPage() {
  const { data, error: queryError } = useEngagements();
  const engagements = data ?? null;
  const error = queryError
    ? queryError instanceof Error
      ? queryError.message
      : String(queryError)
    : null;

  const [filter, setFilter] = useState<EngagementFilter>("default");

  const pending =
    engagements?.filter((e) => isPendingEngagement(e)) ?? [];
  const active =
    engagements?.filter(
      (e) => e.status === "active" && !isPendingEngagement(e),
    ) ?? [];
  const archived =
    engagements?.filter((e) => e.status === "archived") ?? [];
  const workspaceTotal = pending.length + active.length;

  // Toggle behaviour: click a tile to switch to its filter; click the
  // same tile again to revert to default.
  const tileClick = (id: EngagementFilter) =>
    setFilter((prev) => (prev === id ? "default" : id));

  // Which sections should render for the current filter.
  const showPending =
    filter === "default" || filter === "pending";
  const showActive =
    filter === "default" || filter === "active";
  const showArchived = filter === "archived";

  return (
    <div className="space-y-8">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Engagements</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {engagements === null
              ? "Loading…"
              : `${workspaceTotal} ${workspaceTotal === 1 ? "engagement" : "engagements"} in the workspace`}
          </p>
        </div>
        <Button asChild>
          <Link href="/new">
            <Plus className="mr-2 h-4 w-4" />
            New engagement
          </Link>
        </Button>
      </div>

      {/* v2.4.0: 4 clickable filter tiles. Click one to isolate that
          bucket in the grid below; click again to reset. Archived is
          hidden from view by default — the Archived tile is how you
          look at it. */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <FilterTile
          label="Workspace Total"
          value={engagements === null ? "…" : workspaceTotal}
          hint="active + pending"
          icon={<Layers className="h-4 w-4" />}
          tone="neutral"
          active={filter === "default"}
          onClick={() => setFilter("default")}
        />
        <FilterTile
          label="Active Engagements"
          value={engagements === null ? "…" : active.length}
          hint="in progress"
          icon={<Crosshair className="h-4 w-4 text-emerald-500" />}
          tone="active"
          active={filter === "active"}
          onClick={() => tileClick("active")}
        />
        <FilterTile
          label="Pending Engagements"
          value={engagements === null ? "…" : pending.length}
          hint="setup incomplete"
          icon={<Clock className="h-4 w-4 text-amber-500" />}
          tone="pending"
          active={filter === "pending"}
          onClick={() => tileClick("pending")}
        />
        <FilterTile
          label="Archived Engagements"
          value={engagements === null ? "…" : archived.length}
          hint="click to view"
          icon={<Archive className="h-4 w-4 text-muted-foreground" />}
          tone="archived"
          active={filter === "archived"}
          onClick={() => tileClick("archived")}
        />
      </div>

      {error && <p className="text-sm text-critical">{error}</p>}

      {engagements && engagements.length === 0 && !error && (
        <p className="text-sm text-muted-foreground">
          No engagements yet — start one with{" "}
          <Link href="/new" className="underline">
            New engagement
          </Link>{" "}
          or follow the{" "}
          <Link href="/settings/getting-started" className="underline">
            Quick Start guide
          </Link>
          .
        </p>
      )}

      {/* Pending Engagements banner — amber. Rendered when filter is
          default or pending. */}
      {showPending && pending.length > 0 && (
        <section className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
          <div className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-amber-500" />
            <h2 className="text-sm font-semibold">Pending Engagements</h2>
            <span className="text-xs text-muted-foreground">
              · {pending.length} awaiting setup
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Add scope and a strategy revision (or wait for the start date)
            to activate.
          </p>
          <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {pending.map((eng) => (
              <div key={eng.id} className="relative">
                <EngagementCard eng={eng} hideStatusBadge />
                <span className="pointer-events-none absolute right-3 top-3 rounded-full border border-amber-500/40 bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-700 dark:text-amber-300">
                  needs {pendingReason(eng)?.replace("-", " ")}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Active Engagements banner — green. Rendered when filter is
          default or active. */}
      {showActive && active.length > 0 && (
        <section className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4">
          <div className="flex items-center gap-2">
            <Crosshair className="h-4 w-4 text-emerald-500" />
            <h2 className="text-sm font-semibold">Active Engagements</h2>
            <span className="text-xs text-muted-foreground">
              · {active.length} in progress
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Scope defined, strategy in place, start date reached. Runs can
            dispatch.
          </p>
          <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {active.map((eng) => (
              <EngagementCard key={eng.id} eng={eng} />
            ))}
          </div>
        </section>
      )}

      {/* Archived Engagements banner — muted. Only rendered when the
          Archived tile is toggled on. */}
      {showArchived && (
        <section className="rounded-lg border border-border bg-card/40 p-4">
          <div className="flex items-center gap-2">
            <Archive className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-semibold">Archived Engagements</h2>
            <span className="text-xs text-muted-foreground">
              · {archived.length} archived
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Read-only. Reopen from the engagement page&apos;s Unarchive
            control to move back into the workspace.
          </p>
          {archived.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">
              No archived engagements.
            </p>
          ) : (
            <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {archived.map((eng) => (
                <EngagementCard key={eng.id} eng={eng} />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

type FilterTone = "neutral" | "active" | "pending" | "archived";

const TONE_ACTIVE_RING: Record<FilterTone, string> = {
  neutral: "ring-foreground/40",
  active: "ring-emerald-500/70",
  pending: "ring-amber-500/70",
  archived: "ring-muted-foreground/50",
};

function FilterTile({
  label,
  value,
  hint,
  icon,
  tone,
  active,
  onClick,
}: {
  label: string;
  value: number | string;
  hint: string;
  icon: ReactNode;
  tone: FilterTone;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-lg border border-border bg-card/40 p-4 text-left transition-colors hover:bg-card/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active && `ring-2 ${TONE_ACTIVE_RING[tone]}`,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        <span className="text-muted-foreground">{icon}</span>
      </div>
      <p className="mt-3 text-3xl font-semibold tracking-tight tabular-nums">
        {value}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
    </button>
  );
}
