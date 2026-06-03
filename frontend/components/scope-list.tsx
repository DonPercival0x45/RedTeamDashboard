"use client";

// Read-only scope view. Adding/removing scope items happens via
// `rtd engagement scope {add,remove}` against the same backend.

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { listScope } from "@/lib/api";
import { useSources } from "@/lib/source-context";
import type { ScopeItem } from "@/lib/types";

export function ScopeList({ slug }: { slug: string }) {
  const { current } = useSources();
  const [items, setItems] = useState<ScopeItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!current) return;
    try {
      setError(null);
      setItems(await listScope(current, slug));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [current, slug]);

  useEffect(() => {
    setItems(null);
    reload();
  }, [reload, current?.id]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Scope</CardTitle>
        <CardDescription>
          Items that the backend&apos;s scope gate enforces. Manage scope via{" "}
          <code>rtd engagement scope add/remove</code>.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <p className="text-sm text-destructive">{error}</p>}

        {items === null && !error && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}

        {items && items.length === 0 && (
          <p className="rounded border border-amber-500/40 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            No scope yet — runs will be silently denied until at least one
            include is added.
          </p>
        )}

        {items && items.length > 0 && (
          <ul className="divide-y">
            {items.map((item) => (
              <li
                key={item.id}
                className="flex items-center gap-3 py-2"
              >
                <Badge
                  variant={item.is_exclusion ? "destructive" : "secondary"}
                >
                  {item.kind}
                  {item.is_exclusion ? " · exclude" : ""}
                </Badge>
                <span className="font-mono text-sm">{item.value}</span>
                {item.note && (
                  <span className="text-xs text-muted-foreground">
                    {item.note}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
