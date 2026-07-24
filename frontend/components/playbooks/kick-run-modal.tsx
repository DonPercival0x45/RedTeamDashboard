"use client";

// v3 Track A — kick a playbook run. The analyst picks from the engagement's
// EXISTING non-exclusion scope items (never re-typed), chooses the executor
// (internal / mcp), and hits Kick. The backend independently re-validates
// every submitted value against the engagement scope before queuing, so this
// picker is a convenience, not the security boundary.
//
// Backend returns 202 with the pending row (or awaiting_approval for active
// playbooks); the parent's usePlaybookRuns hook re-fetches immediately via
// the mutation's onSuccess invalidation.

import Link from "next/link";
import { useMemo, useState } from "react";
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
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useCreatePlaybookRunMutation, useScope } from "@/lib/hooks";
import type {
  PlaybookExecutorKind,
  PlaybookRead,
  ScopeItem,
} from "@/lib/types";

function kindLabel(item: ScopeItem): string {
  return item.kind.toUpperCase();
}

export function KickRunModal({
  engagementSlug,
  playbook,
  onClose,
}: {
  engagementSlug: string;
  playbook: PlaybookRead;
  onClose: () => void;
}) {
  const create = useCreatePlaybookRunMutation(engagementSlug);
  const scopeQuery = useScope(engagementSlug);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [executor, setExecutor] = useState<PlaybookExecutorKind>("internal");
  const [error, setError] = useState<string | null>(null);

  // Only non-exclusion scope items are runnable targets.
  const scopeItems = useMemo(
    () => (scopeQuery.data ?? []).filter((s) => !s.is_exclusion),
    [scopeQuery.data],
  );
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
        executor,
      });
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

            {loading ? (
              <p className="flex items-center gap-2 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> Loading scope…
              </p>
            ) : loadError ? (
              <p className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
                Could not load scope —{" "}
                {loadError instanceof Error ? loadError.message : "try again."}
              </p>
            ) : scopeItems.length === 0 ? (
              <div className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                <p>No in-scope targets on this engagement yet.</p>
                <p className="mt-1">
                  Add scope on the{" "}
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

          <div className="space-y-2">
            <Label>Executor</Label>
            <div className="grid grid-cols-2 gap-2">
              {(["internal", "mcp"] as PlaybookExecutorKind[]).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  onClick={() => setExecutor(kind)}
                  className={`rounded-md border px-3 py-2 text-left text-xs transition-colors ${
                    executor === kind
                      ? "border-primary bg-primary/10"
                      : "border-border hover:bg-muted"
                  }`}
                >
                  <div className="font-medium uppercase text-[10px] tracking-wide">
                    {kind}
                  </div>
                  <div className="mt-0.5 text-muted-foreground text-[11px]">
                    {kind === "internal"
                      ? "In-process (default)"
                      : "MCP server"}
                  </div>
                </button>
              ))}
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
