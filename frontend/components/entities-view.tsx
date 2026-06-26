"use client";

import { useCallback, useEffect, useState } from "react";
import { Search, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { listEntities } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Entity, Severity } from "@/lib/types";

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
  info: 0,
};

const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "border-critical/50 bg-critical/15 text-critical",
  high: "border-zinc-500/40 text-zinc-100",
  medium: "border-zinc-600/40 text-zinc-300",
  low: "border-zinc-700/40 text-zinc-400",
  info: "border-zinc-800 text-zinc-500",
};

const TYPE_LABEL: Record<string, string> = {
  email: "Email",
  ip: "IP",
  cidr: "CIDR",
  domain: "Domain",
  subdomain: "Subdomain",
  url: "URL",
  host: "Host",
};

function typeLabel(t: string): string {
  return TYPE_LABEL[t] ?? t;
}

// CHARTER Idea 4: entities correlated across the Project's findings —
// searchable, filterable by type, clickable into provenance.
export function EntitiesView({ slug }: { slug: string }) {
  const [entities, setEntities] = useState<Entity[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [type, setType] = useState<string>("all");
  const [selected, setSelected] = useState<Entity | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      setEntities(await listEntities(slug));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [slug]);

  useEffect(() => {
    setEntities(null);
    load();
  }, [load]);

  if (error) return <p className="text-sm text-critical">{error}</p>;
  if (entities === null)
    return <p className="text-sm text-muted-foreground">Loading entities…</p>;

  const types = ["all", ...Array.from(new Set(entities.map((e) => e.type)))];
  const q = search.trim().toLowerCase();
  const visible = entities
    .filter((e) => type === "all" || e.type === type)
    .filter((e) => !q || e.value.toLowerCase().includes(q))
    .sort(
      (a, b) =>
        SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] ||
        b.count - a.count,
    );

  return (
    <div className="space-y-5">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search entities by value…"
          className="pl-9"
        />
      </div>

      <div className="flex flex-wrap gap-1">
        {types.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setType(t)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-xs transition-colors",
              type === t
                ? "border-critical/50 bg-critical/10 text-foreground"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {t === "all" ? "All types" : typeLabel(t)}
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {entities.length
            ? "No entities match these filters."
            : "No entities found yet — they surface as findings come in."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th className="px-3 py-2 w-28">Type</th>
                <th className="px-3 py-2">Value</th>
                <th className="px-3 py-2 w-20">Findings</th>
                <th className="px-3 py-2 w-24">Severity</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((e) => (
                <tr
                  key={`${e.type}:${e.value}`}
                  onClick={() => setSelected(e)}
                  className="cursor-pointer border-b border-border/60 last:border-0 hover:bg-secondary/40"
                >
                  <td className="px-3 py-2.5">
                    <span className="text-xs text-muted-foreground">
                      {typeLabel(e.type)}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs">{e.value}</td>
                  <td className="px-3 py-2.5 tabular-nums text-muted-foreground">
                    {e.count}
                  </td>
                  <td className="px-3 py-2.5">
                    <Badge variant="outline" className={SEVERITY_CLASS[e.severity]}>
                      {e.severity}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <EntitySlideOver entity={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function EntitySlideOver({
  entity,
  onClose,
}: {
  entity: Entity;
  onClose: () => void;
}) {
  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/60" onClick={onClose} aria-hidden />
      <aside className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col overflow-y-auto border-l border-border bg-popover p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">
              {typeLabel(entity.type)}
            </div>
            <h2 className="mt-1 break-all font-mono text-lg font-semibold leading-tight">
              {entity.value}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="mt-3 flex items-center gap-2">
          <Badge variant="outline" className={SEVERITY_CLASS[entity.severity]}>
            {entity.severity}
          </Badge>
          <span className="text-xs text-muted-foreground">
            seen in {entity.count} finding{entity.count === 1 ? "" : "s"}
          </span>
        </div>

        <h3 className="mt-6 text-sm font-medium">Disclosed by</h3>
        <ul className="mt-2 space-y-2">
          {entity.findings.map((f) => (
            <li
              key={f.id}
              className="rounded-md border border-border px-3 py-2 text-sm"
            >
              <div className="font-medium leading-tight">{f.title}</div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {f.tool ?? "—"} · {f.phase} · {f.severity}
              </div>
            </li>
          ))}
        </ul>
      </aside>
    </>
  );
}
