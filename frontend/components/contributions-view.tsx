"use client";

// Contributions tab (v0.10.0). GitHub-style activity heatmap for one
// engagement, sitting above a filterable activity table. The heatmap
// aggregates two data sources — audit_log rows + agent_executions rows
// — and stays anchored at the top of the pane. Clicking a cell filters
// the table below to that day; default (nothing clicked) shows today.

import { useMemo, useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  useContributionsEntries,
  useContributionsHeatmap,
} from "@/lib/hooks";
import type {
  ContributionActor,
  ContributionEntry,
  ContributionHeatmap,
  ContributionSource,
} from "@/lib/types";

const CELL_SIZE = 12;
const CELL_GAP = 3;
const MONTH_LABEL_H = 16;
const DOW_LABEL_W = 24;
const SHADE_STEPS = 5; // 0..4 buckets; 0 = empty

const SOURCE_OPTIONS: { value: ContributionSource | ""; label: string }[] = [
  { value: "", label: "All sources" },
  { value: "audit", label: "Audit log" },
  { value: "agent_exec", label: "Agent runs" },
];

function todayUtcIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function shadeFor(count: number, max: number): string {
  // Zero = neutral background; then 4 escalating blues matching the
  // GitHub palette but keyed to our dark theme.
  if (count === 0 || max === 0) return "rgb(38,45,58)"; // secondary/40
  const ratio = count / max;
  const bucket = Math.min(
    SHADE_STEPS - 1,
    Math.max(1, Math.ceil(ratio * (SHADE_STEPS - 1))),
  );
  const palette = [
    "rgb(38,45,58)", // 0 — unused, kept for indexing
    "rgb(45,90,155)",
    "rgb(65,125,205)",
    "rgb(95,160,235)",
    "rgb(130,190,255)",
  ];
  return palette[bucket];
}

function dowIndex(d: Date): number {
  // Convert JS Sunday=0..Saturday=6 to Monday=0..Sunday=6, so Mon is on top.
  const js = d.getUTCDay();
  return (js + 6) % 7;
}

const DOW_LABELS = ["Mon", "", "Wed", "", "Fri", "", ""];
const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function formatCellDate(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  const dow = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getUTCDay()];
  return `${dow}, ${MONTH_NAMES[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
}

interface WeekColumn {
  weekIndex: number;
  monthLabel: string | null; // shown once per month at first week of that month
  cells: { date: string; count: number; row: number }[];
}

function buildGrid(heatmap: ContributionHeatmap): WeekColumn[] {
  const counts = new Map(heatmap.days.map((d) => [d.date, d.count]));
  const start = new Date(heatmap.start_date + "T00:00:00Z");
  const end = new Date(heatmap.end_date + "T00:00:00Z");

  // Snap the visible grid to a Monday so columns are stable weeks.
  const gridStart = new Date(start);
  gridStart.setUTCDate(gridStart.getUTCDate() - dowIndex(start));

  const weeks: WeekColumn[] = [];
  const cursor = new Date(gridStart);
  let weekIndex = 0;
  let lastMonth = -1;
  while (cursor <= end) {
    const week: WeekColumn = {
      weekIndex,
      monthLabel: null,
      cells: [],
    };
    // Label the first week whose Monday lands in a new month.
    if (cursor.getUTCMonth() !== lastMonth) {
      week.monthLabel = MONTH_NAMES[cursor.getUTCMonth()];
      lastMonth = cursor.getUTCMonth();
    }
    for (let row = 0; row < 7; row++) {
      if (cursor >= start && cursor <= end) {
        const iso = cursor.toISOString().slice(0, 10);
        week.cells.push({ date: iso, count: counts.get(iso) ?? 0, row });
      }
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    weeks.push(week);
    weekIndex++;
  }
  return weeks;
}

// ── component ──────────────────────────────────────────────────────────────

export function ContributionsView({ slug }: { slug: string }) {
  const [selectedDate, setSelectedDate] = useState<string>(todayUtcIso());
  const [actorId, setActorId] = useState<string>("");
  const [source, setSource] = useState<ContributionSource | "">("");
  const [hoverCell, setHoverCell] = useState<{
    date: string;
    count: number;
  } | null>(null);

  // v1.0.0: react-query owns both fetches, keyed by (slug, filters). Changing
  // a filter chip just changes the query key, so cached results for prior
  // filter combinations stay warm — click-back is instant.
  const heatmapQuery = useContributionsHeatmap(slug, {
    actorId: actorId || null,
    source: source || null,
  });
  const heatmap = heatmapQuery.data ?? null;
  const heatmapError = heatmapQuery.error
    ? heatmapQuery.error instanceof Error
      ? heatmapQuery.error.message
      : String(heatmapQuery.error)
    : null;

  const entriesQuery = useContributionsEntries(slug, {
    date: selectedDate,
    actorId: actorId || null,
    source: source || null,
  });
  const entries = entriesQuery.data ?? null;
  const entriesError = entriesQuery.error
    ? entriesQuery.error instanceof Error
      ? entriesQuery.error.message
      : String(entriesQuery.error)
    : null;
  const entriesLoading = entriesQuery.isLoading;

  const weeks = useMemo(
    () => (heatmap ? buildGrid(heatmap) : []),
    [heatmap],
  );

  const totalWidth =
    DOW_LABEL_W + weeks.length * (CELL_SIZE + CELL_GAP) + CELL_GAP;
  const totalHeight = MONTH_LABEL_H + 7 * (CELL_SIZE + CELL_GAP) + CELL_GAP;

  const analystActors = (heatmap?.actors ?? []).filter((a) => a.kind === "analyst");
  const agentActors = (heatmap?.actors ?? []).filter((a) => a.kind === "agent");

  return (
    <div className="space-y-5">
      {/* Heatmap card — always anchored at the top of the tab per spec. */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="text-base">Contributions</CardTitle>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <ActorFilter
                actors={analystActors}
                agentActors={agentActors}
                value={actorId}
                onChange={setActorId}
              />
              <select
                value={source}
                onChange={(e) => setSource(e.target.value as ContributionSource | "")}
                className="h-8 rounded-md border border-border bg-background px-2 text-xs"
              >
                {SOURCE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => {
                  setActorId("");
                  setSource("");
                  setSelectedDate(todayUtcIso());
                }}
                className="h-8 rounded-md border border-border px-2 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground"
              >
                Reset
              </button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="pt-2">
          {heatmapError && (
            <p className="text-xs text-critical">{heatmapError}</p>
          )}
          {!heatmap && !heatmapError && (
            <p className="text-xs text-muted-foreground">Loading heatmap…</p>
          )}
          {heatmap && (
            <>
              <div className="overflow-x-auto">
                <svg
                  width={totalWidth}
                  height={totalHeight}
                  className="block"
                  role="img"
                  aria-label="Engagement contribution heatmap"
                >
                  {/* Day-of-week labels — left column */}
                  {DOW_LABELS.map((lbl, i) =>
                    lbl ? (
                      <text
                        key={i}
                        x={0}
                        y={MONTH_LABEL_H + i * (CELL_SIZE + CELL_GAP) + CELL_SIZE - 2}
                        fontSize={9}
                        fill="rgb(150,160,175)"
                      >
                        {lbl}
                      </text>
                    ) : null,
                  )}
                  {/* Month labels — top row (rendered per week that starts a new month) */}
                  {weeks.map((w) =>
                    w.monthLabel ? (
                      <text
                        key={`m-${w.weekIndex}`}
                        x={DOW_LABEL_W + w.weekIndex * (CELL_SIZE + CELL_GAP)}
                        y={11}
                        fontSize={10}
                        fill="rgb(180,190,205)"
                      >
                        {w.monthLabel}
                      </text>
                    ) : null,
                  )}
                  {/* Cells */}
                  {weeks.map((w) =>
                    w.cells.map((cell) => (
                      <rect
                        key={cell.date}
                        x={DOW_LABEL_W + w.weekIndex * (CELL_SIZE + CELL_GAP)}
                        y={MONTH_LABEL_H + cell.row * (CELL_SIZE + CELL_GAP)}
                        width={CELL_SIZE}
                        height={CELL_SIZE}
                        rx={2}
                        fill={shadeFor(cell.count, heatmap.max_count)}
                        stroke={
                          cell.date === selectedDate
                            ? "rgb(255,140,60)"
                            : "transparent"
                        }
                        strokeWidth={cell.date === selectedDate ? 1.5 : 0}
                        className="cursor-pointer"
                        onClick={() => setSelectedDate(cell.date)}
                        onMouseEnter={() =>
                          setHoverCell({ date: cell.date, count: cell.count })
                        }
                        onMouseLeave={() => setHoverCell(null)}
                      >
                        <title>
                          {cell.count === 0
                            ? `No contributions — ${formatCellDate(cell.date)}`
                            : `${cell.count} contribution${cell.count === 1 ? "" : "s"} — ${formatCellDate(cell.date)}`}
                        </title>
                      </rect>
                    )),
                  )}
                </svg>
              </div>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
                <span>
                  {hoverCell
                    ? hoverCell.count === 0
                      ? `No contributions — ${formatCellDate(hoverCell.date)}`
                      : `${hoverCell.count} contribution${hoverCell.count === 1 ? "" : "s"} — ${formatCellDate(hoverCell.date)}`
                    : `Selected: ${formatCellDate(selectedDate)}`}
                </span>
                <span className="flex items-center gap-1.5">
                  Less
                  {[0, 1, 2, 3, 4].map((i) => (
                    <span
                      key={i}
                      className="inline-block h-3 w-3 rounded-sm"
                      style={{
                        background:
                          i === 0
                            ? "rgb(38,45,58)"
                            : shadeFor(
                                Math.ceil((i / 4) * Math.max(heatmap.max_count, 1)),
                                Math.max(heatmap.max_count, 1),
                              ),
                      }}
                    />
                  ))}
                  More
                </span>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Activity list for the selected day (or range in future). */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <CardTitle className="text-base">
            Activity — {formatCellDate(selectedDate)}
          </CardTitle>
          <span className="text-xs text-muted-foreground">
            {entries ? `${entries.total} entr${entries.total === 1 ? "y" : "ies"}` : ""}
          </span>
        </CardHeader>
        <CardContent>
          {entriesError && (
            <p className="text-xs text-critical">{entriesError}</p>
          )}
          {entriesLoading && !entries && (
            <p className="text-xs text-muted-foreground">Loading…</p>
          )}
          {entries && entries.entries.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No contributions recorded for this day / filter.
            </p>
          )}
          {entries && entries.entries.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="border-b border-border/60 text-left text-muted-foreground">
                  <tr>
                    <th className="py-2 pr-3 font-normal">When</th>
                    <th className="py-2 pr-3 font-normal">Who</th>
                    <th className="py-2 pr-3 font-normal">Source</th>
                    <th className="py-2 pr-3 font-normal">Action</th>
                    <th className="py-2 pr-3 font-normal">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.entries.map((e, idx) => (
                    <EntryRow key={`${e.when}-${idx}`} entry={e} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function EntryRow({ entry }: { entry: ContributionEntry }) {
  const kindClass =
    entry.actor_kind === "analyst"
      ? "text-sky-200"
      : entry.actor_kind === "agent"
        ? "text-purple-200"
        : "text-muted-foreground";
  const when = new Date(entry.when).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  return (
    <tr className="border-b border-border/30 last:border-none">
      <td className="py-2 pr-3 font-mono text-[11px] text-muted-foreground">
        {when}
      </td>
      <td className={`py-2 pr-3 ${kindClass}`}>
        {entry.actor_label}
        <span className="ml-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
          {entry.actor_kind}
        </span>
      </td>
      <td className="py-2 pr-3 text-muted-foreground">
        {entry.source === "audit" ? "audit" : "agent"}
      </td>
      <td className="py-2 pr-3 font-mono text-[11px]">{entry.action}</td>
      <td className="py-2 pr-3">{entry.summary}</td>
    </tr>
  );
}

function ActorFilter({
  actors,
  agentActors,
  value,
  onChange,
}: {
  actors: ContributionActor[];
  agentActors: ContributionActor[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-8 rounded-md border border-border bg-background px-2 text-xs"
    >
      <option value="">All actors</option>
      {actors.length > 0 && (
        <optgroup label="Analysts">
          {actors.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
            </option>
          ))}
        </optgroup>
      )}
      {agentActors.length > 0 && (
        <optgroup label="Agents">
          {agentActors.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
            </option>
          ))}
        </optgroup>
      )}
    </select>
  );
}
