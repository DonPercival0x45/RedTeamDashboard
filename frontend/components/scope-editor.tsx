"use client";

import { useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { importScope } from "@/lib/api";
import {
  qk,
  useCreateScopeItemMutation,
  useDeleteScopeItemMutation,
  useScope,
} from "@/lib/hooks";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ScopeImporter } from "@/components/scope-importer";
import type { ScopeItem, ScopeKind } from "@/lib/types";

const KINDS: ScopeKind[] = ["domain", "cidr", "ip", "url", "email"];

export function ScopeEditor({
  slug,
  canWrite,
}: {
  slug: string;
  canWrite: boolean;
}) {
  // v1.0.0: react-query owns the fetch. Add/delete mutations invalidate
  // the scope cache; importer calls qc.invalidateQueries directly.
  const qc = useQueryClient();
  const { data: items, error: queryError, isLoading } = useScope(slug);
  const createMutation = useCreateScopeItemMutation(slug);
  const deleteMutation = useDeleteScopeItemMutation(slug);

  const [kind, setKind] = useState<ScopeKind>("domain");
  const [value, setValue] = useState("");
  const [isExclusion, setIsExclusion] = useState(false);
  const [isFound, setIsFound] = useState(false);
  const [note, setNote] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ScopeItem | null>(null);
  const error =
    localError ??
    (queryError instanceof Error
      ? queryError.message
      : queryError
        ? String(queryError)
        : null);
  const adding = createMutation.isPending;

  const onAdd = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!value.trim()) return;
    setLocalError(null);
    try {
      await createMutation.mutateAsync({
        kind,
        value: value.trim(),
        is_exclusion: isExclusion,
        note: note.trim() || null,
        source: isFound ? "found" : "defined",
      });
      setValue("");
      setNote("");
      setIsExclusion(false);
      setIsFound(false);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  const onDelete = async () => {
    if (!pendingDelete) return;
    setLocalError(null);
    try {
      await deleteMutation.mutateAsync(pendingDelete.id);
      setPendingDelete(null);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <>
    <Card>
      <CardHeader>
        <CardTitle>Scope</CardTitle>
        <CardDescription>
          Tool calls that fall outside these items are denied by the gate
          before they ever run. Exclusions override includes.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {canWrite && (
          <ScopeImporter
            onCommit={async (text) => {
              const result = await importScope(slug, text);
              await Promise.all([
                qc.invalidateQueries({ queryKey: qk.scope(slug) }),
                qc.invalidateQueries({ queryKey: qk.entities(slug) }),
                qc.invalidateQueries({ queryKey: ["stored-entities", slug] }),
                qc.invalidateQueries({ queryKey: qk.engagements() }),
              ]);
              if (result.errors.length || result.duplicates.length) {
                const bits = [
                  `${result.created.length} added`,
                  result.duplicates.length
                    ? `${result.duplicates.length} duplicates skipped`
                    : null,
                  result.errors.length
                    ? `${result.errors.length} unparseable`
                    : null,
                ].filter(Boolean);
                setLocalError(bits.join(" · "));
              } else {
                setLocalError(null);
              }
            }}
          />
        )}
        {canWrite && (
        <form
          onSubmit={onAdd}
          className="grid gap-3 sm:grid-cols-[7rem_1fr_auto] sm:items-end"
        >
          <div className="space-y-2">
            <Label htmlFor="scope-kind">Kind</Label>
            <select
              id="scope-kind"
              value={kind}
              onChange={(event) => setKind(event.target.value as ScopeKind)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="scope-value">Value</Label>
            <Input
              id="scope-value"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder={
                kind === "domain"
                  ? "acme.com"
                  : kind === "cidr"
                    ? "10.0.0.0/24"
                    : kind === "ip"
                      ? "10.0.0.5"
                      : kind === "email"
                        ? "analyst@acme.com"
                        : "https://acme.com/login"
              }
              required
            />
          </div>
          <Button type="submit" disabled={adding}>
            {adding ? "Adding…" : "Add"}
          </Button>
          <label className="flex items-center gap-2 text-sm sm:col-span-2">
            <input
              type="checkbox"
              checked={isExclusion}
              onChange={(event) => setIsExclusion(event.target.checked)}
              className="h-4 w-4 rounded border-input"
            />
            Exclusion (carves out from a broader include above)
          </label>
          <label className="flex items-center gap-2 text-sm sm:col-span-2">
            <input
              type="checkbox"
              checked={isFound}
              onChange={(event) => setIsFound(event.target.checked)}
              className="h-4 w-4 rounded border-input"
            />
            Found (turned up in findings, not original client scope)
          </label>
          <Input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="optional note"
            className="sm:col-span-3"
          />
        </form>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}

        {isLoading && items === undefined && !error && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}

        {items && items.length === 0 && (
          <p className="rounded border border-amber-500/40 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            No scope yet — runs will be silently denied until you add at least
            one include. Try <code>kind: domain · value: acme.com</code> or review{" "}
            <Link href="/settings/getting-started" className="font-medium underline">
              Quick Start
            </Link>
            .
          </p>
        )}

        {items && items.length > 0 && (
          <ul className="divide-y">
            {items.map((item) => {
              const found = item.source === "found";
              return (
              <li
                key={item.id}
                className={cn(
                  "flex items-center justify-between py-2 px-2 -mx-2 rounded",
                  found && "bg-emerald-500/10",
                )}
              >
                <div className="flex items-center gap-3">
                  <Badge
                    variant={item.is_exclusion ? "destructive" : "secondary"}
                  >
                    {item.kind}
                    {item.is_exclusion ? " · exclude" : ""}
                  </Badge>
                  <span className="font-mono text-sm">{item.value}</span>
                  {!item.is_exclusion &&
                    item.effective_scope?.state === "excluded" && (
                      <span
                        className="rounded-full border border-amber-500/50 bg-amber-500/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-amber-700 dark:text-amber-300"
                        title={item.effective_scope.reason}
                      >
                        shadowed by exclusion
                      </span>
                    )}
                  {found && (
                    <span
                      className="rounded-full border border-emerald-500/50 bg-emerald-500/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-emerald-600 dark:text-emerald-400"
                      title="Added from findings, not original client scope"
                    >
                      found
                    </span>
                  )}
                  {item.note && (
                    <span className="text-xs text-muted-foreground">
                      {item.note}
                    </span>
                  )}
                </div>
                {canWrite && (
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setPendingDelete(item)}
                    aria-label={`Delete scope item ${item.value}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
    <ConfirmDialog
      open={pendingDelete !== null}
      title="Delete scope item?"
      description={
        <>
          <p>
            <span className="font-mono text-foreground">{pendingDelete?.value}</span>{" "}
            will be removed from scope and from eligible targets for new playbook runs.
          </p>
          {pendingDelete?.is_exclusion ? (
            <p className="mt-2 text-amber-700 dark:text-amber-300">
              This is an exclusion. Removing it may authorize targets covered by a broader include.
            </p>
          ) : null}
        </>
      }
      busy={deleteMutation.isPending}
      onConfirm={onDelete}
      onOpenChange={(open) => !open && setPendingDelete(null)}
    />
    </>
  );
}
