"use client";

// Read-only list of active per-(engagement, tool) session grants. Revoke
// happens via `rtd grants revoke <id>` against the same backend.

import { useCallback, useEffect, useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { listAuthorizations } from "@/lib/api";
import { useSources } from "@/lib/source-context";
import type { Authorization } from "@/lib/types";

interface GrantsCardProps {
  engagementId: string;
  // Bumping this triggers a refetch — parent does so when SSE surfaces a new
  // tool.auto_approved (a fresh grant may have appeared this session).
  refreshKey: number;
}

export function GrantsCard({ engagementId, refreshKey }: GrantsCardProps) {
  const { current } = useSources();
  const [grants, setGrants] = useState<Authorization[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!current) return;
    setLoading(true);
    try {
      const rows = await listAuthorizations(current, engagementId, true);
      setGrants(rows);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [current, engagementId]);

  useEffect(() => {
    reload();
  }, [reload, refreshKey]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Session grants</CardTitle>
        <CardDescription>
          Active per-tool standing approvals. Revoke via{" "}
          <code>rtd grants revoke &lt;id&gt;</code>.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error && (
          <p className="mb-2 text-sm text-destructive">{error}</p>
        )}
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading grants…</p>
        ) : grants.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No active session grants.
          </p>
        ) : (
          <ul className="space-y-2">
            {grants.map((grant) => (
              <li
                key={grant.id}
                className="space-y-1 rounded border bg-muted/40 px-3 py-2 text-sm"
              >
                <div className="font-mono">{grant.tool_name}</div>
                <div className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                  <span>
                    granted {new Date(grant.created_at).toLocaleString()}
                  </span>
                  <code>{grant.id}</code>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
