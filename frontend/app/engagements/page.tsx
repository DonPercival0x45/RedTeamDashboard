"use client";

// v2.0.0: this WAS app/page.tsx (the landing engagement chooser).
// Now it lives at /engagements — the primary route the LeftSidebar
// links to. The old app/page.tsx is a server-side redirect to here.
//
// Added the design's 3-up stat banner (Total / Active / Archived)
// above the engagement grid. Numbers come straight from
// useEngagements() so they always match the cards below.

import Link from "next/link";
import { Archive, Crosshair, Layers, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { EngagementCard } from "@/components/engagement-card";
import { useEngagements } from "@/lib/hooks";
import type { Engagement } from "@/lib/types";

export default function EngagementListPage() {
  // v1.0.0: react-query owns the fetch. Focus revalidation catches
  // freshly-created engagements from another tab / analyst.
  const { data, error: queryError } = useEngagements();
  const engagements = data ?? null;
  const error = queryError
    ? queryError instanceof Error
      ? queryError.message
      : String(queryError)
    : null;

  return (
    <div className="space-y-8">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Engagements</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {engagements === null
              ? "Loading…"
              : `${engagements.length} ${
                  engagements.length === 1 ? "engagement" : "engagements"
                }`}
          </p>
        </div>
        <Button asChild>
          <Link href="/new">
            <Plus className="mr-2 h-4 w-4" />
            New engagement
          </Link>
        </Button>
      </div>

      {/* v2.0.0: 3-up stat banner. Renders skeleton values while the
          engagements query is in flight so the layout doesn't shift
          once counts arrive. */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard
          label="Total Engagements"
          value={engagements === null ? "…" : engagements.length}
          hint="all time"
          icon={<Layers className="h-4 w-4" />}
        />
        <StatCard
          label="Active Engagements"
          value={engagements === null ? "…" : countByStatus(engagements, "active")}
          hint="in progress"
          icon={<Crosshair className="h-4 w-4 text-critical" />}
        />
        <StatCard
          label="Archived Engagements"
          value={engagements === null ? "…" : countByStatus(engagements, "archived")}
          hint="completed"
          icon={<Archive className="h-4 w-4" />}
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

      {engagements && engagements.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {engagements.map((eng) => (
            <EngagementCard key={eng.id} eng={eng} />
          ))}
        </div>
      )}
    </div>
  );
}

function countByStatus(
  engagements: Engagement[],
  status: Engagement["status"],
): number {
  return engagements.filter((eng) => eng.status === status).length;
}

function StatCard({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: number | string;
  hint: string;
  icon: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-card/40 p-4">
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
    </div>
  );
}
