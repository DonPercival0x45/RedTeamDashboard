"use client";

// Read-only list of pending approvals. Decisions go through
// `rtd approve <id>` (or `--deny`) against the same backend. The viewer
// just surfaces what's blocking so the operator knows to act.

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { listApprovals } from "@/lib/api";
import { useSources } from "@/lib/source-context";
import type { Approval } from "@/lib/types";

interface Props {
  slug: string;
  // Bumped after the SSE stream surfaces a new approval.pending — pages pass
  // this to trigger a refetch without rewriting their SSE handler.
  refreshKey: number;
}

export function ApprovalsList({ slug, refreshKey }: Props) {
  const { current } = useSources();
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!current) return;
    try {
      setError(null);
      setApprovals(await listApprovals(current, slug, "pending"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [current, slug]);

  useEffect(() => {
    setApprovals(null);
    reload();
  }, [reload, current?.id, refreshKey]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Pending approvals</CardTitle>
        <CardDescription>
          Tool calls waiting on operator decision. Approve, edit, or deny via{" "}
          <code>rtd approve &lt;id&gt;</code>.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error && <p className="mb-2 text-sm text-destructive">{error}</p>}
        {approvals === null && !error && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {approvals && approvals.length === 0 && (
          <p className="text-sm text-muted-foreground">
            Nothing pending. Active/destructive tool calls will appear here.
          </p>
        )}
        {approvals && approvals.length > 0 && (
          <ul className="space-y-2">
            {approvals.map((approval) => (
              <li
                key={approval.id}
                className="space-y-1 rounded border bg-muted/40 px-3 py-2"
              >
                <div className="flex items-center gap-2 text-sm">
                  <Badge variant="outline">{approval.risk}</Badge>
                  <span className="font-mono">{approval.tool_name}</span>
                  <code className="ml-auto text-xs text-muted-foreground">
                    {approval.id}
                  </code>
                </div>
                <pre className="overflow-x-auto rounded bg-background p-2 font-mono text-xs">
                  {JSON.stringify(approval.tool_args, null, 2)}
                </pre>
                <p className="text-xs text-muted-foreground">
                  <code>rtd approve {approval.id}</code> ·{" "}
                  <code>rtd approve {approval.id} --deny</code>
                </p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
