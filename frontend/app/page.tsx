"use client";

import Link from "next/link";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EngagementCard } from "@/components/engagement-card";
import { useEngagements } from "@/lib/hooks";

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

      {error && <p className="text-sm text-critical">{error}</p>}

      {engagements && engagements.length === 0 && !error && (
        <p className="text-sm text-muted-foreground">
          No engagements yet — start one with{" "}
          <Link href="/new" className="underline">
            New engagement
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
