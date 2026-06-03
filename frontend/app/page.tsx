"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { listEngagements } from "@/lib/api";
import { useSources } from "@/lib/source-context";
import type { Engagement } from "@/lib/types";

export default function EngagementListPage() {
  const { current } = useSources();
  const [engagements, setEngagements] = useState<Engagement[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!current) return;
    try {
      setError(null);
      setEngagements(await listEngagements(current));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [current]);

  // Reset + refetch every time the source changes so we never render stale
  // data for the wrong tenant.
  useEffect(() => {
    setEngagements(null);
    reload();
  }, [reload, current?.id]);

  if (!current) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a source to view engagements.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Engagements</CardTitle>
          <CardDescription>
            Read-only view from <code>{current.name}</code>. Create and
            archive engagements via <code>rtd engagement create</code>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error && (
            <p className="mb-3 text-sm text-destructive">{error}</p>
          )}
          {engagements === null && !error && (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
          {engagements && engagements.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No engagements yet. Create one with{" "}
              <code>rtd engagement create &quot;Acme Q1&quot;</code>.
            </p>
          )}
          {engagements && engagements.length > 0 && (
            <ul className="divide-y">
              {engagements.map((eng) => (
                <li
                  key={eng.id}
                  className="flex items-center justify-between py-3"
                >
                  <div>
                    <Link
                      href={`/e/${eng.slug}`}
                      className="font-medium hover:underline"
                    >
                      {eng.name}
                    </Link>
                    <p className="text-xs text-muted-foreground">{eng.slug}</p>
                  </div>
                  <Badge
                    variant={
                      eng.status === "active"
                        ? "default"
                        : eng.status === "archived"
                          ? "secondary"
                          : "outline"
                    }
                  >
                    {eng.status}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
