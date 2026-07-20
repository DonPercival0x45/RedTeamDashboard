"use client";

// v2.4.0 — "Who used what" table for the Status tab. Aggregates
// AgentExecution rows by (acting_user_id, agent, model) and lets the
// analyst re-sort by Model / Agent / User / Cost by clicking a header
// (same UX as the sortable columns on Findings). Rows without an
// acting user (planner + scheduled system runs) render as "system".

import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { useEngagementAttribution } from "@/lib/hooks";
import type { AttributionRow } from "@/lib/api";

type SortKey = "model" | "agent" | "user" | "cost";

const AGENT_LABELS: Record<string, string> = {
  strategic: "Strategic",
  tactical: "Tactical",
  planner: "Planner",
  triage: "Triage",
  tool_review: "Tool Review",
  correlate: "Correlate",
  engagement_strategist: "Engagement Strategist",
};

const HEADERS: { key: SortKey; label: string; align?: "right" }[] = [
  { key: "model", label: "Model" },
  { key: "agent", label: "Agent" },
  { key: "user", label: "User" },
  { key: "cost", label: "Cost", align: "right" },
];

export function AttributionTable({ slug }: { slug: string }) {
  const { data, isLoading, error } = useEngagementAttribution(slug);
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({
    key: "cost",
    desc: true,
  });

  const rows = useMemo<AttributionRow[]>(() => data ?? [], [data]);

  const sorted = useMemo(() => {
    const cmp = (a: AttributionRow, b: AttributionRow): number => {
      switch (sort.key) {
        case "model":
          return (a.model_name ?? "").localeCompare(b.model_name ?? "");
        case "agent":
          return (a.agent ?? "").localeCompare(b.agent ?? "");
        case "user":
          return (a.user_display ?? "￿").localeCompare(
            b.user_display ?? "￿",
          );
        case "cost":
          return a.cost_usd - b.cost_usd;
      }
    };
    const copy = [...rows];
    copy.sort((a, b) => (sort.desc ? -cmp(a, b) : cmp(a, b)));
    return copy;
  }, [rows, sort]);

  const toggleSort = (key: SortKey) =>
    setSort((prev) =>
      prev.key === key ? { key, desc: !prev.desc } : { key, desc: true },
    );

  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold">Attribution</h3>
        <span className="text-xs text-muted-foreground">
          · who used what — {rows.length} {rows.length === 1 ? "bucket" : "buckets"}
        </span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        One row per (user, agent, model) combination. Click a column
        header to re-sort.
      </p>

      {isLoading && (
        <p className="mt-3 text-xs text-muted-foreground">Loading…</p>
      )}
      {error && !isLoading && (
        <p className="mt-3 text-xs text-destructive">
          Could not load attribution.
        </p>
      )}
      {!isLoading && !error && rows.length === 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          No agent executions yet for this engagement.
        </p>
      )}
      {!isLoading && !error && rows.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                {HEADERS.map((h) => {
                  const active = sort.key === h.key;
                  return (
                    <th
                      key={h.key}
                      className={cn(
                        "py-2",
                        h.align === "right" && "text-right",
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => toggleSort(h.key)}
                        aria-pressed={active}
                        className={cn(
                          "inline-flex items-center gap-1 rounded-sm px-1 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          active && "text-foreground",
                        )}
                      >
                        <span>{h.label}</span>
                        {active && (
                          <span className="text-[10px]">
                            {sort.desc ? "▼" : "▲"}
                          </span>
                        )}
                      </button>
                    </th>
                  );
                })}
                <th className="py-2 text-right text-muted-foreground">Runs</th>
                <th className="py-2 text-right text-muted-foreground">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, index) => {
                const modelLabel =
                  row.model_name ??
                  (row.model_provider ? `${row.model_provider}:?` : "—");
                const userLabel = row.user_display ?? "system";
                const agentLabel =
                  AGENT_LABELS[row.agent] ?? row.agent;
                return (
                  <tr
                    key={`${row.user_id ?? "sys"}-${row.agent}-${row.model_provider ?? ""}-${row.model_name ?? ""}-${index}`}
                    className="border-b border-border/60"
                  >
                    <td className="py-2 font-mono text-[11px]">
                      {modelLabel}
                      {row.model_provider && (
                        <span className="ml-1 text-muted-foreground">
                          · {row.model_provider}
                        </span>
                      )}
                    </td>
                    <td className="py-2">{agentLabel}</td>
                    <td className="py-2">
                      {row.user_display ? (
                        <span>{userLabel}</span>
                      ) : (
                        <span className="text-muted-foreground">system</span>
                      )}
                    </td>
                    <td className="py-2 text-right tabular-nums">
                      ${row.cost_usd.toFixed(4)}
                    </td>
                    <td className="py-2 text-right tabular-nums text-muted-foreground">
                      {row.executions}
                    </td>
                    <td className="py-2 text-right tabular-nums text-muted-foreground">
                      {row.tokens_in + row.tokens_out}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
