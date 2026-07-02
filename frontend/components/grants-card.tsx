"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  useAuthorizations,
  useRevokeAuthorizationMutation,
} from "@/lib/hooks";
import type { Authorization } from "@/lib/types";

interface GrantsCardProps {
  engagementId: string;
  // Bumping this triggers a refetch — parent does so after a "remember"
  // approval (a new grant may have appeared) and after this card revokes one.
  refreshKey: number;
  canRevoke: boolean;
}

export function GrantsCard({
  engagementId,
  refreshKey,
  canRevoke,
}: GrantsCardProps) {
  // v1.0.0: react-query owns the grants list. `refreshKey` (parent bumps
  // it after a "remember" approval) still forces a refetch via the effect
  // below.
  const { data, isLoading, error: queryError, refetch } = useAuthorizations(
    engagementId,
    true,
  );
  const grants = data ?? [];
  const revokeMutation = useRevokeAuthorizationMutation(engagementId);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const error =
    localError ??
    (queryError instanceof Error
      ? queryError.message
      : queryError
        ? String(queryError)
        : null);
  const loading = isLoading;

  useEffect(() => {
    void refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const onRevoke = async (grant: Authorization) => {
    if (
      !window.confirm(
        `Revoke session grant for ${grant.tool_name}? Future calls will prompt for approval again.`,
      )
    ) {
      return;
    }
    setBusyId(grant.id);
    setLocalError(null);
    try {
      await revokeMutation.mutateAsync(grant.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Session grants</CardTitle>
        <CardDescription>
          Per-tool standing approvals. While active, in-scope calls to that tool
          auto-run instead of prompting.
          {canRevoke ? " Revoke to require approval again." : null}
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
            No active session grants. Approve an active tool with “Remember for
            this session” to create one.
          </p>
        ) : (
          <ul className="space-y-2">
            {grants.map((grant) => (
              <li
                key={grant.id}
                className="flex items-center justify-between rounded border bg-muted/40 px-3 py-2"
              >
                <div className="text-sm">
                  <div className="font-mono">{grant.tool_name}</div>
                  <div className="text-xs text-muted-foreground">
                    granted {new Date(grant.created_at).toLocaleString()}
                  </div>
                </div>
                {canRevoke && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busyId === grant.id}
                    onClick={() => onRevoke(grant)}
                  >
                    {busyId === grant.id ? "Revoking…" : "Revoke"}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
