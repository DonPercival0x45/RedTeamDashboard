"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  GitBranch,
  Search,
  ShieldCheck,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { QueryState } from "@/components/query-state";
import { buildAttackPathProjection } from "@/lib/attack-paths";
import { engagementEntityHref } from "@/lib/engagement-links";
import { useFindings } from "@/lib/hooks";

function label(value: string): string {
  return value.replaceAll("_", " ");
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function AttackPathsView({ slug }: { slug: string }) {
  const findingsQuery = useFindings(slug);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "validation">("all");
  const projection = useMemo(
    () => buildAttackPathProjection(findingsQuery.data ?? []),
    [findingsQuery.data],
  );
  const paths = projection.paths;
  const normalized = query.trim().toLowerCase();
  const visible = paths.filter(
    (path) =>
      (filter === "all" || path.needsValidation || path.disputed) &&
      (!normalized ||
        path.nodes.some((node) => node.value.toLowerCase().includes(normalized)) ||
        path.edges.some(
          (edge) =>
            label(edge.relation).includes(normalized) ||
            edge.citations.some(
              (citation) =>
                citation.title.toLowerCase().includes(normalized) ||
                citation.tool?.toLowerCase().includes(normalized),
            ),
        )),
  );
  const attentionCount = paths.filter(
    (path) => path.needsValidation || path.disputed,
  ).length;
  const citedFindings = new Set(
    paths.flatMap((path) =>
      path.edges.flatMap((edge) => edge.citations.map((citation) => citation.findingId)),
    ),
  ).size;

  if (findingsQuery.data === undefined) {
    return (
      <QueryState
        isLoading={findingsQuery.isLoading}
        error={findingsQuery.error}
        loadingLabel="Building evidence-backed paths…"
        errorLabel="Could not build attack paths because findings are unavailable."
        onRetry={() => void findingsQuery.refetch()}
        isRetrying={findingsQuery.isFetching}
      />
    );
  }

  return (
    <div className="space-y-5">
      <header>
        <div className="flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-semibold">Attack paths</h2>
        </div>
        <p className="mt-1 max-w-4xl text-sm text-muted-foreground">
          Deterministic paths assembled only from structured relationship evidence. Paths help explain pivots; they do not establish ownership, scope, exploitability, or authorization.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <Card><CardContent className="p-4"><p className="text-2xl font-semibold">{paths.length}</p><p className="text-xs text-muted-foreground">Evidence paths</p></CardContent></Card>
        <Card><CardContent className="p-4"><p className="text-2xl font-semibold">{attentionCount}</p><p className="text-xs text-muted-foreground">Need source review</p></CardContent></Card>
        <Card><CardContent className="p-4"><p className="text-2xl font-semibold">{citedFindings}</p><p className="text-xs text-muted-foreground">Cited findings</p></CardContent></Card>
      </div>

      <div className="space-y-3 border-b border-border bg-background py-3 lg:sticky lg:top-0 lg:z-10">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="Search attack paths"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Find a host, IP, relationship, source tool, or finding"
            className="pl-9"
          />
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            aria-pressed={filter === "all"}
            onClick={() => setFilter("all")}
            className="rounded-md border border-border px-3 py-1.5 text-xs aria-pressed:bg-muted"
          >
            All ({paths.length})
          </button>
          <button
            type="button"
            aria-pressed={filter === "validation"}
            onClick={() => setFilter("validation")}
            className="rounded-md border border-border px-3 py-1.5 text-xs aria-pressed:bg-muted"
          >
            Needs review ({attentionCount})
          </button>
        </div>
      </div>

      {projection.truncated && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
          Showing the first 500 deterministic paths. Search or inspect source relationships to narrow broad branches.
        </div>
      )}

      {paths.length === 0 ? (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
            No structured relationship paths are available yet. DNS inventory playbooks can supply observed relationship evidence.
          </CardContent>
        </Card>
      ) : visible.length === 0 ? (
        <Card><CardContent className="p-6 text-sm text-muted-foreground">No paths match the current search and filter.</CardContent></Card>
      ) : (
        <div className="max-h-[calc(100vh-18rem)] space-y-3 overflow-y-auto pr-2">
          {visible.map((path) => (
            <Card key={path.id}>
              <CardHeader className="pb-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <CardTitle className="flex items-center gap-2 text-sm">
                    {path.disputed || path.needsValidation ? (
                      <AlertTriangle className="h-4 w-4 text-amber-500" />
                    ) : (
                      <ShieldCheck className="h-4 w-4 text-emerald-500" />
                    )}
                    {path.edges.length} relationship {path.edges.length === 1 ? "step" : "steps"}
                  </CardTitle>
                  <div className="flex gap-2">
                    {path.disputed && <Badge variant="destructive">Disputed source</Badge>}
                    {!path.disputed && path.needsValidation && (
                      <Badge variant="outline">Needs validation</Badge>
                    )}
                    <Badge variant="outline">{path.maxSeverity}</Badge>
                    <Badge variant="secondary">{path.citationCount} citations</Badge>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  {path.nodes.map((node, index) => (
                    <div key={`${node.type}:${node.value}`} className="contents">
                      {index > 0 && <ArrowRight className="h-4 w-4 text-muted-foreground" />}
                      <Link
                        href={engagementEntityHref(slug, node)}
                        className="rounded-md border border-border bg-muted/30 px-2.5 py-1.5 font-mono text-xs hover:border-primary"
                      >
                        <span className="mr-1 text-muted-foreground">{node.type}</span>
                        {node.value}
                      </Link>
                    </div>
                  ))}
                </div>
                <ol className="space-y-3 border-l border-border pl-4">
                  {path.edges.map((edge) => (
                    <li key={edge.id} className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <Badge variant="outline">
                          {edge.disputed ? "Rejected source record" : "Observed"}
                        </Badge>
                        <span className="font-mono">{edge.sourceValue}</span>
                        <span className="text-muted-foreground">{label(edge.relation)}</span>
                        <span className="font-mono">{edge.targetValue}</span>
                        <span className="text-muted-foreground">last seen {formatDate(edge.lastSeen)}</span>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {edge.citations.map((citation) => (
                          <Link
                            key={citation.findingId}
                            href={`/e/findings/${citation.findingId}?slug=${encodeURIComponent(slug)}&returnTo=${encodeURIComponent(`/e?slug=${slug}&view=attack-paths`)}`}
                            className="rounded-md border border-border px-2 py-1 text-xs hover:border-primary"
                          >
                            {citation.title} · {label(citation.status)}
                            {citation.exclusion ? ` · ${label(citation.exclusion)}` : ""}
                          </Link>
                        ))}
                      </div>
                    </li>
                  ))}
                </ol>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
