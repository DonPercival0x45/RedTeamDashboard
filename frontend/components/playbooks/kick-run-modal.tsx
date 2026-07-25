"use client";

// v3 Track A — kick a playbook run. The analyst picks from the engagement's
// EXISTING non-exclusion scope items (never re-typed) and hits Kick. The
// catalog selects a compatible executor; the backend independently re-validates
// every submitted value against the engagement scope before queuing, so this
// picker is a convenience, not the security boundary.
//
// Backend returns 202 with the pending row (or awaiting_approval for active
// playbooks); the parent's usePlaybookRuns hook re-fetches immediately via
// the mutation's onSuccess invalidation.

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { QueryState } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useCreatePlaybookRunMutation, useScope } from "@/lib/hooks";
import type { PlaybookRead, ScopeItem } from "@/lib/types";

function kindLabel(item: ScopeItem): string {
  return item.kind.toUpperCase();
}

export function KickRunModal({
  engagementSlug,
  playbook,
  initialTarget,
  onStarted,
  onClose,
}: {
  engagementSlug: string;
  playbook: PlaybookRead;
  initialTarget?: { type: string; value: string } | null;
  onStarted?: () => void;
  onClose: () => void;
}) {
  const create = useCreatePlaybookRunMutation(engagementSlug);
  const scopeQuery = useScope(engagementSlug);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const initialTargetHandled = useRef(false);

  // Only currently effective includes compatible with the playbook asset
  // class are offered. The backend computes effective scope with the same
  // matcher used at execution time, so exclusions disappear immediately.
  const scopeItems = useMemo(
    () =>
      (scopeQuery.data ?? []).filter(
        (item) =>
          !item.is_exclusion &&
          item.is_effectively_in_scope !== false &&
          item.kind === playbook.applies_to_asset_class,
      ),
    [playbook.applies_to_asset_class, scopeQuery.data],
  );

  // Entity launch hints are untrusted UI state. Preselect only an exact target
  // returned by the authoritative effective-scope endpoint and compatible with
  // this recipe. Derived children covered only by broader rules remain opt-in.
  useEffect(() => {
    if (
      initialTargetHandled.current ||
      scopeQuery.data === undefined ||
      !initialTarget
    ) {
      return;
    }
    initialTargetHandled.current = true;
    if (
      initialTarget.type === playbook.applies_to_asset_class &&
      scopeItems.some((item) => item.value === initialTarget.value)
    ) {
      setSelected(new Set([initialTarget.value]));
    }
  }, [initialTarget, playbook.applies_to_asset_class, scopeItems, scopeQuery.data]);

  // A scope row may be deleted or become excluded while this dialog is open.
  // Do not retain an invisible stale selection or submit an empty subset.
  useEffect(() => {
    const eligible = new Set(scopeItems.map((item) => item.value));
    setSelected((previous) => {
      const next = new Set([...previous].filter((value) => eligible.has(value)));
      return next.size === previous.size ? previous : next;
    });
  }, [scopeItems]);
  const loading = scopeQuery.isLoading;
  const loadError = scopeQuery.error;

  const toggle = (value: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  };
  const allSelected =
    scopeItems.length > 0 && selected.size === scopeItems.length;
  const toggleAll = () => {
    setSelected((prev) =>
      allSelected ? new Set() : new Set(scopeItems.map((s) => s.value)),
    );
  };

  const canSubmit = selected.size > 0 && !create.isPending && !loading;

  const submit = async () => {
    setError(null);
    try {
      await create.mutateAsync({
        playbook_slug: playbook.slug,
        playbook_version: playbook.version,
        scope_subset: scopeItems
          .filter((s) => selected.has(s.value))
          .map((s) => s.value),
        executor: playbook.required_executor,
      });
      onStarted?.();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to kick run.");
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Kick playbook run</DialogTitle>
          <DialogDescription>
            Select authorized targets from this engagement&apos;s scope.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <p className="text-sm font-medium">{playbook.name}</p>
            <p className="text-xs text-muted-foreground">
              v{playbook.version} · {playbook.step_count} steps ·{" "}
              {playbook.applies_to_asset_class}
              {playbook.active
                ? " · gated (analyst approval required)"
                : ""}
            </p>
          </div>

          {(playbook.step_preview?.length ?? 0) > 0 ? (
            <div className="space-y-2 rounded-md border border-border bg-muted/20 p-3">
              <Label>Execution plan</Label>
              <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
                {playbook.step_preview?.map((step, index) => (
                  <li key={`${index}-${step}`}>{step}</li>
                ))}
              </ol>
              <p className="text-xs text-muted-foreground">
                {selected.size > 0
                  ? `At least ${selected.size * playbook.step_count} tool calls for the current selection.`
                  : "Select targets to calculate the minimum tool calls."}
                {playbook.expands_targets
                  ? " Authorized discoveries may add later calls; every expanded target is checked against current exclusions."
                  : ""}
              </p>
            </div>
          ) : null}

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Targets in scope ({selected.size} selected)</Label>
              {scopeItems.length > 0 && (
                <button
                  type="button"
                  onClick={toggleAll}
                  className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                >
                  {allSelected ? "Clear all" : "Select all"}
                </button>
              )}
            </div>

            <QueryState
              isLoading={scopeQuery.data === undefined && loading}
              error={loadError}
              hasData={scopeQuery.data !== undefined}
              loadingLabel="Loading scope…"
              errorLabel="Could not load scope targets."
              onRetry={() => void scopeQuery.refetch()}
              isRetrying={scopeQuery.isFetching}
              compact={scopeQuery.data !== undefined}
            />
            {scopeQuery.data === undefined ? null : scopeItems.length === 0 ? (
              <div className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                <p>
                  No included {playbook.applies_to_asset_class} targets are
                  available for this playbook.
                </p>
                <p className="mt-1">
                  Add a compatible target on the{" "}
                  <Link
                    href={`/e?slug=${encodeURIComponent(
                      engagementSlug,
                    )}&view=scope`}
                    className="font-medium text-foreground underline underline-offset-2"
                  >
                    Scope tab
                  </Link>{" "}
                  first, then kick the playbook.
                </p>
              </div>
            ) : (
              <ul className="max-h-56 space-y-1 overflow-y-auto rounded-md border border-border p-2">
                {scopeItems.map((item) => (
                  <li key={item.id}>
                    <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-xs hover:bg-muted/60">
                      <input
                        type="checkbox"
                        checked={selected.has(item.value)}
                        onChange={() => toggle(item.value)}
                        className="h-3.5 w-3.5 accent-current"
                      />
                      <span className="rounded border border-border bg-muted/50 px-1 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                        {kindLabel(item)}
                      </span>
                      <span className="font-mono">{item.value}</span>
                      {item.note ? (
                        <span className="truncate text-muted-foreground">
                          · {item.note}
                        </span>
                      ) : null}
                    </label>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-xs text-muted-foreground">
              The runner iterates each step against every selected target.
              Targets must already be in scope — add new ones on the Scope tab.
            </p>
          </div>

          {(playbook.required_credentials?.length ?? 0) > 0 ? (
            <div className="space-y-2">
              <Label>Required credentials</Label>
              <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
                <p>
                  This run needs requester-owned credentials for{" "}
                  <span className="font-medium">
                    {playbook.required_credentials?.join(", ")}
                  </span>
                  .
                </p>
                <Link
                  href="/settings/keys"
                  className="mt-1 inline-block font-medium underline underline-offset-2"
                >
                  Review keys
                </Link>
              </div>
            </div>
          ) : null}

          <div className="space-y-2">
            <Label>Execution path</Label>
            <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs">
              <div className="font-medium">Selected automatically</div>
              <div className="mt-0.5 text-muted-foreground">
                {(playbook.execution_paths?.length ?? 0) > 0
                  ? playbook.execution_paths?.join(" + ")
                  : playbook.required_executor === "mcp"
                    ? "Connected service"
                    : "Built-in"}
                {(playbook.execution_paths?.length ?? 0) > 1
                  ? " — each step is routed to its server-approved transport."
                  : " collection."}
              </div>
            </div>
          </div>

          {error ? (
            <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>
          ) : null}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" size="sm" disabled={create.isPending}>
              Cancel
            </Button>
          </DialogClose>
          <Button size="sm" onClick={submit} disabled={!canSubmit}>
            {create.isPending ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : null}
            Kick run
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
