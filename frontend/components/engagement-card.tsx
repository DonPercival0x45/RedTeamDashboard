"use client";

// Engagement list card with v1.4.5 scope quick-actions: a scope-size
// summary line ("3 in scope · 1 exclusion") and a one-click "Add to
// scope" inline form so analysts don't have to dive into the engagement
// to seed a domain / host / CIDR. The whole card is a link into the
// engagement; the quick-add form stops propagation so it doesn't trigger
// the navigation.

import { useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronUp, Plus } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createScopeItem } from "@/lib/api";
import { qk } from "@/lib/hooks";
import type { Engagement, ScopeKind } from "@/lib/types";

const KIND_OPTIONS: ScopeKind[] = ["domain", "ip", "cidr", "url"];

function statusVariant(status: Engagement["status"]) {
  if (status === "active") return "default" as const;
  if (status === "archived") return "secondary" as const;
  return "outline" as const;
}

export function EngagementCard({ eng }: { eng: Engagement }) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [kind, setKind] = useState<ScopeKind>("domain");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isExclusion, setIsExclusion] = useState(false);

  const scopeCount = eng.scope_count ?? 0;
  const exclusionCount = eng.exclusion_count ?? 0;
  const hasCounts =
    typeof eng.scope_count === "number" ||
    typeof eng.exclusion_count === "number";

  const onAdd = async (event: React.FormEvent) => {
    event.preventDefault();
    event.stopPropagation();
    if (!value.trim()) return;
    setError(null);
    setBusy(true);
    try {
      await createScopeItem(eng.slug, {
        kind,
        value: value.trim(),
        is_exclusion: isExclusion,
      });
      // Refresh both the scope list and the engagement cards so the
      // count pill updates immediately.
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.scope(eng.slug) }),
        qc.invalidateQueries({ queryKey: qk.engagements() }),
      ]);
      setValue("");
      setIsExclusion(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="group relative rounded-lg border border-border bg-card p-5 transition-colors hover:border-muted-foreground/40">
      <Link
        href={`/e?slug=${encodeURIComponent(eng.slug)}`}
        className="absolute inset-0"
        aria-label={`Open ${eng.name}`}
      />
      <div className="relative flex items-start justify-between gap-3">
        <h2 className="font-medium leading-tight group-hover:text-foreground">
          {eng.name}
        </h2>
        <Badge variant={statusVariant(eng.status)}>{eng.status}</Badge>
      </div>
      <p className="relative mt-2 font-mono text-xs text-muted-foreground">
        {eng.slug}
      </p>

      {hasCounts && (
        <p className="relative mt-3 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{scopeCount}</span> in
          scope
          {exclusionCount > 0 && (
            <>
              {" · "}
              <span className="font-medium text-foreground">
                {exclusionCount}
              </span>{" "}
              exclusion{exclusionCount === 1 ? "" : "s"}
            </>
          )}
        </p>
      )}

      <div className="relative mt-3 flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          {hasCounts && scopeCount === 0 && exclusionCount === 0
            ? "no scope yet"
            : "scope set"}
        </span>
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground"
        >
          {expanded ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          )}
          {expanded ? "Hide" : "Add to scope"}
        </button>
      </div>

      {expanded && (
        <form
          onSubmit={onAdd}
          className="relative mt-2 space-y-2 border-t border-border pt-3"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex gap-2">
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as ScopeKind)}
              disabled={busy}
              className="rounded-md border border-border bg-background px-2 py-1 text-xs"
            >
              {KIND_OPTIONS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
            <Input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={`e.g. ${
                kind === "domain"
                  ? "acme.com"
                  : kind === "cidr"
                    ? "10.0.0.0/24"
                    : kind === "ip"
                      ? "203.0.113.5"
                      : "https://acme.com"
              }`}
              disabled={busy}
              className="h-7 flex-1 text-xs"
            />
          </div>
          <div className="flex items-center justify-between gap-2">
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={isExclusion}
                onChange={(e) => setIsExclusion(e.target.checked)}
                disabled={busy}
                className="h-3 w-3"
              />
              exclusion
            </label>
            <Button
              type="submit"
              size="sm"
              disabled={busy || !value.trim()}
              className="h-7 px-2 text-xs"
            >
              <Plus className="mr-1 h-3 w-3" />
              {busy ? "Adding…" : "Add"}
            </Button>
          </div>
          {error && <p className="text-xs text-critical">{error}</p>}
        </form>
      )}
    </div>
  );
}
