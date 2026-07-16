"use client";

// v2.5.0 — Analytics page. Four panels + Engagement Log.
//
// Layout mirrors the Webpage Layout Redesign mockup:
//   1. Findings over time  — Line / Bar / Pie toggle, last 12 weeks
//   2. Severity breakdown  — Line / Bar / Pie toggle
//   3. Scan coverage       — big % + progress bar
//   4. Top findings        — 3 most-severe recent findings
//   5. Engagement Log      — scrollable feed of engagement-level actions
//
// Charts render with Recharts (added as a dep in v2.5.0). All chart
// colors resolve to our existing theme tokens (severity classes for
// severity bars, ember red for the primary line) so light/dark/hicontrast
// themes still work.

import { useMemo, useState } from "react";
import { Download } from "lucide-react";
import { CopyJsonButton } from "@/components/copy-json-button";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import { DateTime } from "@/components/date-time";
import { cn } from "@/lib/utils";
import {
  useAnalyticsEngagementLog,
  useAnalyticsFindingsOverTime,
  useAnalyticsScanCoverage,
  useAnalyticsSeverityBreakdown,
  useAnalyticsTopFindings,
  useEngagements,
} from "@/lib/hooks";
import type {
  AnalyticsPeriod,
  EngagementLogRow,
  FindingsOverTimeOpts,
  SeverityBreakdownRow,
  TopFindingRow,
  WeekBucket,
} from "@/lib/api";

type ChartMode = "line" | "bar" | "pie";

const SEVERITY_ORDER: SeverityBreakdownRow["severity"][] = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
];

const SEVERITY_HEX: Record<SeverityBreakdownRow["severity"], string> = {
  critical: "#dc2626",
  high: "#f59e0b",
  medium: "#3b82f6",
  low: "#94a3b8",
  info: "#64748b",
};

export default function AnalyticsPage() {
  const engagementsQuery = useEngagements();
  const engagements = engagementsQuery.data ?? [];
  const [selected, setSelected] = useState<string>("all");
  const filter = selected === "all" ? null : selected;

  // v2.5.2: time-range picker for Findings-over-time. Daily/weekly/
  // monthly are fixed windows (last N buckets). Custom collects a
  // start/end date pair; backend picks bucket size automatically.
  const [period, setPeriod] = useState<AnalyticsPeriod>("week");
  const [customStart, setCustomStart] = useState<string>("");
  const [customEnd, setCustomEnd] = useState<string>("");
  const overTimeOpts: FindingsOverTimeOpts = useMemo(() => {
    if (period === "custom") {
      return customStart && customEnd
        ? { period: "custom", start: customStart, end: customEnd }
        : { period: "week", points: 12 };
    }
    return { period, points: period === "day" ? 30 : 12 };
  }, [period, customStart, customEnd]);
  const overTimeQuery = useAnalyticsFindingsOverTime(filter, overTimeOpts);
  const severityQuery = useAnalyticsSeverityBreakdown(filter);
  const coverageQuery = useAnalyticsScanCoverage(filter);
  const topFindingsQuery = useAnalyticsTopFindings(filter);
  const logQuery = useAnalyticsEngagementLog(filter);

  const onExport = () => {
    const payload = {
      exported_at: new Date().toISOString(),
      engagement: selected,
      findings_over_time: overTimeQuery.data ?? [],
      severity_breakdown: severityQuery.data ?? [],
      scan_coverage: coverageQuery.data ?? null,
      top_findings: topFindingsQuery.data ?? [],
      engagement_log: logQuery.data ?? [],
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `analytics-${selected}-${new Date()
      .toISOString()
      .replace(/[:.]/g, "-")}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Analytics</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Findings across your engagements.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="all">All engagements</option>
            {engagements.map((eng) => (
              <option key={eng.slug} value={eng.slug}>
                {eng.name}
              </option>
            ))}
          </select>
          <Button variant="outline" size="sm" onClick={onExport}>
            <Download className="mr-1.5 h-4 w-4" />
            Export
          </Button>
        </div>
      </div>

      <FindingsOverTimePanel
        engagementLabel={selected === "all" ? "All engagements" : selected}
        data={overTimeQuery.data ?? []}
        loading={overTimeQuery.isLoading}
        period={period}
        setPeriod={setPeriod}
        customStart={customStart}
        setCustomStart={setCustomStart}
        customEnd={customEnd}
        setCustomEnd={setCustomEnd}
      />

      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <SeverityBreakdownPanel
          data={severityQuery.data ?? []}
          loading={severityQuery.isLoading}
        />
        <div className="space-y-4">
          <ScanCoveragePanel
            data={coverageQuery.data ?? null}
            loading={coverageQuery.isLoading}
          />
          <TopFindingsPanel
            data={topFindingsQuery.data ?? []}
            loading={topFindingsQuery.isLoading}
          />
        </div>
      </div>

      <EngagementLogPanel
        data={logQuery.data ?? []}
        loading={logQuery.isLoading}
      />
    </div>
  );
}

function FindingsOverTimePanel({
  engagementLabel,
  data,
  loading,
  period,
  setPeriod,
  customStart,
  setCustomStart,
  customEnd,
  setCustomEnd,
}: {
  engagementLabel: string;
  data: WeekBucket[];
  loading: boolean;
  period: AnalyticsPeriod;
  setPeriod: (p: AnalyticsPeriod) => void;
  customStart: string;
  setCustomStart: (v: string) => void;
  customEnd: string;
  setCustomEnd: (v: string) => void;
}) {
  const [mode, setMode] = useState<ChartMode>("line");
  const rangeLabel =
    period === "day"
      ? "last 30 days"
      : period === "week"
        ? "last 12 weeks"
        : period === "month"
          ? "last 12 months"
          : customStart && customEnd
            ? `${customStart} → ${customEnd}`
            : "custom (pick start + end)";
  // Explicit `color` — otherwise Recharts inherits the default text
  // color which is black, invisible against `--card` on dark mode. Also
  // used by labelStyle + itemStyle below so both the key and value are
  // visible regardless of theme.
  const tooltipStyle = {
    background: "hsl(var(--card))",
    border: "1px solid hsl(var(--border))",
    borderRadius: "6px",
    fontSize: "12px",
    color: "hsl(var(--foreground))",
  } as const;
  const tooltipTextStyle = { color: "hsl(var(--foreground))" } as const;
  return (
    <section className="rounded-lg border border-border bg-card/40 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Findings over time</h2>
          <p className="mt-1 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
            {engagementLabel} · {rangeLabel}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value as AnalyticsPeriod)}
            className="h-8 rounded-md border border-input bg-background px-2 text-xs"
            aria-label="Time range"
          >
            <option value="day">Daily</option>
            <option value="week">Weekly</option>
            <option value="month">Monthly</option>
            <option value="custom">Custom</option>
          </select>
          {period === "custom" && (
            <div className="flex items-center gap-1">
              <input
                type="date"
                value={customStart}
                onChange={(e) => setCustomStart(e.target.value)}
                className="h-8 rounded-md border border-input bg-background px-2 text-xs"
                aria-label="Custom start date"
              />
              <span className="text-xs text-muted-foreground">→</span>
              <input
                type="date"
                value={customEnd}
                onChange={(e) => setCustomEnd(e.target.value)}
                min={customStart || undefined}
                className="h-8 rounded-md border border-input bg-background px-2 text-xs"
                aria-label="Custom end date"
              />
            </div>
          )}
          <ModeToggle mode={mode} setMode={setMode} />
        </div>
      </div>
      <div className="mt-4 h-64 w-full">
        {loading ? (
          <ChartLoading />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            {mode === "line" ? (
              <LineChart data={data}>
                <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" />
                <XAxis dataKey="label" stroke="hsl(var(--muted-foreground))" fontSize={10} />
                <YAxis stroke="hsl(var(--muted-foreground))" fontSize={10} allowDecimals={false} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipTextStyle} itemStyle={tooltipTextStyle} />
                <Line
                  type="monotone"
                  dataKey="count"
                  stroke="hsl(var(--critical))"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                />
              </LineChart>
            ) : mode === "bar" ? (
              <BarChart data={data}>
                <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" />
                <XAxis dataKey="label" stroke="hsl(var(--muted-foreground))" fontSize={10} />
                <YAxis stroke="hsl(var(--muted-foreground))" fontSize={10} allowDecimals={false} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipTextStyle} itemStyle={tooltipTextStyle} />
                <Bar dataKey="count" fill="hsl(var(--critical))" />
              </BarChart>
            ) : (
              <PieChart>
                <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipTextStyle} itemStyle={tooltipTextStyle} />
                <Pie
                  data={data.filter((d) => d.count > 0)}
                  dataKey="count"
                  nameKey="label"
                  outerRadius={90}
                  label
                >
                  {data.filter((d) => d.count > 0).map((_, i) => (
                    <Cell key={i} fill={`hsl(${(i * 30) % 360}, 60%, 55%)`} />
                  ))}
                </Pie>
              </PieChart>
            )}
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}

function SeverityBreakdownPanel({
  data,
  loading,
}: {
  data: SeverityBreakdownRow[];
  loading: boolean;
}) {
  const [mode, setMode] = useState<ChartMode>("bar");
  const ordered = useMemo(() => {
    const map = new Map(data.map((row) => [row.severity, row.count]));
    return SEVERITY_ORDER.map((sev) => ({
      severity: sev,
      label: sev.charAt(0).toUpperCase() + sev.slice(1),
      count: map.get(sev) ?? 0,
      fill: SEVERITY_HEX[sev],
    }));
  }, [data]);
  // Explicit `color` — otherwise Recharts inherits the default text
  // color which is black, invisible against `--card` on dark mode. Also
  // used by labelStyle + itemStyle below so both the key and value are
  // visible regardless of theme.
  const tooltipStyle = {
    background: "hsl(var(--card))",
    border: "1px solid hsl(var(--border))",
    borderRadius: "6px",
    fontSize: "12px",
    color: "hsl(var(--foreground))",
  } as const;
  const tooltipTextStyle = { color: "hsl(var(--foreground))" } as const;

  return (
    <section className="rounded-lg border border-border bg-card/40 p-5">
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-sm font-semibold">Severity breakdown</h2>
        <ModeToggle mode={mode} setMode={setMode} />
      </div>
      <div className="mt-4 h-64 w-full">
        {loading ? (
          <ChartLoading />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            {mode === "line" ? (
              <LineChart data={ordered}>
                <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" />
                <XAxis dataKey="label" stroke="hsl(var(--muted-foreground))" fontSize={10} />
                <YAxis stroke="hsl(var(--muted-foreground))" fontSize={10} allowDecimals={false} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipTextStyle} itemStyle={tooltipTextStyle} />
                <Line type="monotone" dataKey="count" stroke="hsl(var(--critical))" strokeWidth={2} />
              </LineChart>
            ) : mode === "bar" ? (
              <BarChart data={ordered}>
                <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" />
                <XAxis dataKey="label" stroke="hsl(var(--muted-foreground))" fontSize={10} />
                <YAxis stroke="hsl(var(--muted-foreground))" fontSize={10} allowDecimals={false} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipTextStyle} itemStyle={tooltipTextStyle} />
                <Bar dataKey="count">
                  {ordered.map((row) => (
                    <Cell key={row.severity} fill={row.fill} />
                  ))}
                </Bar>
              </BarChart>
            ) : (
              <PieChart>
                <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipTextStyle} itemStyle={tooltipTextStyle} />
                <Pie
                  data={ordered.filter((row) => row.count > 0)}
                  dataKey="count"
                  nameKey="label"
                  outerRadius={90}
                  label
                >
                  {ordered.filter((row) => row.count > 0).map((row) => (
                    <Cell key={row.severity} fill={row.fill} />
                  ))}
                </Pie>
              </PieChart>
            )}
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}

function ScanCoveragePanel({
  data,
  loading,
}: {
  data: { percent: number; covered: number; total: number } | null;
  loading: boolean;
}) {
  return (
    <section className="rounded-lg border border-border bg-card/40 p-5">
      <h2 className="text-sm font-semibold">Scan coverage</h2>
      {loading ? (
        <p className="mt-3 text-xs text-muted-foreground">Loading…</p>
      ) : !data ? (
        <p className="mt-3 text-xs text-muted-foreground">No data.</p>
      ) : (
        <>
          <div className="mt-3 flex items-baseline gap-1">
            <span className="text-4xl font-semibold tabular-nums">
              {data.percent}
            </span>
            <span className="text-lg text-muted-foreground">%</span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {data.total === 0
              ? "no scope defined yet"
              : `${data.covered} of ${data.total} in-scope targets touched`}
          </p>
          <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-secondary/60">
            <div
              className="h-full rounded-full bg-emerald-500 transition-[width] duration-500"
              style={{ width: `${data.percent}%` }}
            />
          </div>
        </>
      )}
    </section>
  );
}

function TopFindingsPanel({
  data,
  loading,
}: {
  data: TopFindingRow[];
  loading: boolean;
}) {
  return (
    <section className="rounded-lg border border-border bg-card/40 p-5">
      <h2 className="text-sm font-semibold">Top findings</h2>
      {loading ? (
        <p className="mt-3 text-xs text-muted-foreground">Loading…</p>
      ) : data.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">
          No findings yet.
        </p>
      ) : (
        <ul className="mt-3 space-y-3">
          {data.map((f) => (
            <li key={f.id} className="flex items-start gap-2">
              <span
                className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
                style={{ background: SEVERITY_HEX[f.severity] }}
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{f.title}</p>
                <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                  {f.engagement_slug} · <DateTime value={f.created_at} />
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const EVENT_VERB: Record<string, string> = {
  "engagement.created": "created an engagement",
  "mcp.engagement.created": "created an engagement via MCP",
  "engagement.archived": "archived an engagement",
  "mcp.engagement.archived": "archived an engagement via MCP",
  "engagement.unarchived": "unarchived an engagement",
  "engagement.flushed": "deleted an engagement",
  "engagement.updated": "updated an engagement",
  "scope.imported": "imported scope",
  "scope.item.created": "added a scope item",
  "scope.item.deleted": "removed a scope item",
  "mcp.scope.added": "added scope via MCP",
  "findings.imported": "imported findings",
  "finding.created_manual": "added a finding manually",
  "finding.deleted": "deleted a finding",
  "finding.validated": "validated a finding",
  "finding.triaged": "triaged a finding",
  "finding.updated": "updated a finding",
  "findings.bulk_deleted": "bulk-deleted findings",
  "findings.bulk_updated": "bulk-updated findings",
  "findings.merged": "merged findings",
  "entities.imported": "imported entities",
  "scanner_import.committed": "committed a scanner import",
  "run.requested": "requested a run",
  "task.cancelled": "cancelled a task",
  "task.retried": "retried a task",
  "attachment.uploaded": "uploaded an attachment",
  "attachment.deleted": "deleted an attachment",
  "approval.decided": "decided an approval",
  "suggestion.accepted": "accepted a suggestion",
  "suggestion.dismissed": "dismissed a suggestion",
};

function EngagementLogPanel({
  data,
  loading,
}: {
  data: EngagementLogRow[];
  loading: boolean;
}) {
  return (
    <section className="rounded-lg border border-border bg-card/40 p-5">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">Engagement Log</h2>
        <span className="text-xs text-muted-foreground">
          · {data.length} {data.length === 1 ? "event" : "events"} (newest first)
        </span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Every engagement-level action across the workspace. Scroll for
        older events; expand a row for the raw payload.
      </p>
      {loading ? (
        <p className="mt-3 text-xs text-muted-foreground">Loading…</p>
      ) : data.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">
          No engagement activity yet.
        </p>
      ) : (
        <ul className="mt-3 max-h-[28rem] space-y-1 overflow-y-auto pr-1">
          {data.map((row) => (
            <EngagementLogItem key={row.id} row={row} />
          ))}
        </ul>
      )}
    </section>
  );
}

function EngagementLogItem({ row }: { row: EngagementLogRow }) {
  const [open, setOpen] = useState(false);
  const actor =
    row.actor_type === "user"
      ? row.actor_display ?? "unknown user"
      : row.actor_type === "agent"
        ? "agent"
        : row.actor_type === "system"
          ? "system"
          : row.actor_type;
  const verb = EVENT_VERB[row.event_type] ?? row.event_type;
  const engagementLabel = row.engagement_slug ?? "—";
  return (
    <li className="rounded border border-border/60 bg-background/40">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        className="flex w-full items-start justify-between gap-3 px-3 py-2 text-left hover:bg-secondary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="min-w-0 flex-1">
          <p className="text-xs">
            <span className="font-medium">{actor}</span>
            <span className="text-muted-foreground"> {verb}</span>
            {row.engagement_slug && (
              <>
                <span className="text-muted-foreground"> · </span>
                <span className="font-mono text-[10px]">{engagementLabel}</span>
              </>
            )}
          </p>
          <p className="mt-0.5 flex flex-wrap gap-x-3 text-[10px] text-muted-foreground">
            <span>
              <DateTime value={row.created_at} />
            </span>
            {row.engagement_name && <span>{row.engagement_name}</span>}
            {row.engagement_time_frame && (
              <span>time-frame: {row.engagement_time_frame}</span>
            )}
            {row.engagement_status && (
              <span>status: {row.engagement_status}</span>
            )}
          </p>
        </div>
        <span className="mt-1 shrink-0 text-[10px] text-muted-foreground">
          {open ? "▼" : "▶"}
        </span>
      </button>
      {open && (
        <div className="border-t border-border/60 bg-background/60">
          <div className="flex justify-end px-3 pt-2">
            <CopyJsonButton value={row.payload} />
          </div>
          <pre className="max-h-64 overflow-auto px-3 py-2 font-mono text-[10px] leading-snug text-muted-foreground">
            {JSON.stringify(row.payload, null, 2)}
          </pre>
        </div>
      )}
    </li>
  );
}

function ModeToggle({
  mode,
  setMode,
}: {
  mode: ChartMode;
  setMode: (m: ChartMode) => void;
}) {
  const opts: { id: ChartMode; label: string }[] = [
    { id: "line", label: "Line" },
    { id: "bar", label: "Bar" },
    { id: "pie", label: "Pie" },
  ];
  return (
    <div className="flex overflow-hidden rounded-md border border-border">
      {opts.map((opt) => (
        <button
          key={opt.id}
          type="button"
          onClick={() => setMode(opt.id)}
          aria-pressed={mode === opt.id}
          className={cn(
            "px-2.5 py-1 text-xs transition-colors",
            mode === opt.id
              ? "bg-critical text-critical-foreground"
              : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function ChartLoading() {
  return (
    <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
      Loading…
    </div>
  );
}
