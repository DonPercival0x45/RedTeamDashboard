"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Play, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { QueryState } from "@/components/query-state";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { runToolDirect, type ToolRunResult } from "@/lib/api";
import { qk, useScope } from "@/lib/hooks";

const V3_TOOLS = [
  {
    slug: "whois",
    label: "WHOIS",
    description: "Registrar, registrant, and registration-date lookup.",
  },
  {
    slug: "dns_inventory",
    label: "DNS records",
    description: "A, AAAA, CNAME, MX, TXT, and NS enumeration.",
  },
] as const;

type LastRun = {
  ok: boolean;
  findingsNew: number;
  findingsTotal: number;
  error: string | null;
  scope: string;
};

export function V3ToolsPanel({ slug }: { slug: string }) {
  const qc = useQueryClient();
  const scopeQuery = useScope(slug);
  const targets = useMemo(
    () =>
      (scopeQuery.data ?? []).filter(
        (item) => !item.is_exclusion && item.kind === "domain",
      ),
    [scopeQuery.data],
  );
  const [selectedScope, setSelectedScope] = useState("");
  const [runningSlug, setRunningSlug] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<Record<string, LastRun>>({});

  useEffect(() => {
    if (targets.length === 0) {
      setSelectedScope("");
      return;
    }
    if (!targets.some((item) => item.value === selectedScope)) {
      setSelectedScope(targets[0].value);
    }
  }, [selectedScope, targets]);

  const mutation = useMutation({
    mutationFn: ({
      toolSlug,
      scope,
    }: {
      toolSlug: string;
      scope: string;
    }): Promise<ToolRunResult> => runToolDirect(slug, toolSlug, { scope }),
    onMutate: ({ toolSlug }) => setRunningSlug(toolSlug),
    onSuccess: (result) => {
      setLastRun((previous) => ({
        ...previous,
        [result.tool]: {
          ok: result.ok,
          findingsNew: result.findings_new,
          findingsTotal: result.findings_total,
          error: result.error,
          scope: result.scope,
        },
      }));
      void Promise.all([
        qc.invalidateQueries({ queryKey: qk.findings(slug) }),
        qc.invalidateQueries({ queryKey: qk.entities(slug) }),
      ]);
    },
    onError: (error: unknown, variables) => {
      setLastRun((previous) => ({
        ...previous,
        [variables.toolSlug]: {
          ok: false,
          findingsNew: 0,
          findingsTotal: 0,
          error: error instanceof Error ? error.message : String(error),
          scope: variables.scope,
        },
      }));
    },
    onSettled: () => setRunningSlug(null),
  });

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Wrench className="h-4 w-4" />
          Passive tools
        </CardTitle>
        <CardDescription className="text-xs">
          Explicit analyst-triggered lookups against an existing in-scope
          target. Results persist directly to Findings without an LLM.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 pb-4">
        {scopeQuery.data === undefined && (scopeQuery.isLoading || scopeQuery.error) ? (
          <QueryState
            isLoading={scopeQuery.isLoading}
            error={scopeQuery.error}
            loadingLabel="Loading scope targets…"
            errorLabel="Could not load scope targets."
            onRetry={() => void scopeQuery.refetch()}
            isRetrying={scopeQuery.isFetching}
          />
        ) : (
          <>
            <QueryState
              isLoading={false}
              error={scopeQuery.error}
              hasData
              compact
              onRetry={() => void scopeQuery.refetch()}
              isRetrying={scopeQuery.isFetching}
            />
            {targets.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Add an included domain target in Scope before running a tool.
              </p>
            ) : (
              <label className="flex max-w-xl flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Authorized target</span>
                <select
                  value={selectedScope}
                  onChange={(event) => setSelectedScope(event.target.value)}
                  className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                >
                  {targets.map((target) => (
                    <option key={target.id} value={target.value}>
                      {target.value} · {target.kind}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </>
        )}

        <div className="flex flex-wrap gap-3">
          {V3_TOOLS.map((tool) => {
            const busy = runningSlug === tool.slug;
            const result = lastRun[tool.slug];
            return (
              <div key={tool.slug} className="flex min-w-48 flex-col gap-1">
                <button
                  type="button"
                  disabled={Boolean(runningSlug) || !selectedScope}
                  onClick={() => mutation.mutate({ toolSlug: tool.slug, scope: selectedScope })}
                  title={tool.description}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-2 text-xs transition-colors hover:border-foreground/40 hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Play className="h-3 w-3" />
                  <span className="font-medium">{tool.label}</span>
                  {busy && <span className="text-muted-foreground">running…</span>}
                </button>
                {result && !busy && (
                  <div
                    role="status"
                    className={
                      result.ok
                        ? "flex items-start gap-1 text-[10px] text-emerald-600 dark:text-emerald-400"
                        : "flex items-start gap-1 text-[10px] text-destructive"
                    }
                  >
                    {result.ok ? (
                      <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0" />
                    ) : (
                      <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                    )}
                    <span>
                      {result.ok
                        ? `${result.findingsNew} new · ${result.findingsTotal} total on ${result.scope}`
                        : `Failed: ${result.error ?? "unknown error"}`}
                    </span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
