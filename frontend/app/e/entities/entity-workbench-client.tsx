"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Boxes, Clipboard, Network, RefreshCcw, ShieldCheck } from "lucide-react";
import { DateTime } from "@/components/date-time";
import { Badge } from "@/components/ui/badge";
import {
  listEntities,
  listFindings,
  listScope,
  listStoredEntities,
  listTasks,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Entity, Finding, ScopeItem, Severity, StoredEntity, Task } from "@/lib/types";

const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "border-critical/50 bg-critical/15 text-critical",
  high: "border-zinc-500/40 text-zinc-800 dark:text-zinc-100",
  medium: "border-zinc-600/40 text-zinc-600 dark:text-zinc-300",
  low: "border-zinc-700/40 text-zinc-700 dark:text-zinc-400",
  info: "border-zinc-800 text-zinc-700 dark:text-zinc-500",
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

type Tab = "overview" | "findings" | "tools" | "evidence" | "activity";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "findings", label: "Findings" },
  { id: "tools", label: "Tools" },
  { id: "evidence", label: "Evidence / Imports" },
  { id: "activity", label: "Activity" },
];

type ToolAction = { tool: string | null; label: string; prompt: (value: string) => string };

const ACTIONS: Record<string, ToolAction[]> = {
  domain: [
    { tool: "subfinder", label: "Enumerate subdomains", prompt: (v) => `Enumerate subdomains, DNS records, and CT logs for ${v}, then probe what's live.` },
    { tool: "portscan", label: "Port-scan discovered hosts", prompt: (v) => `Run port discovery against hosts discovered under ${v}, then enumerate open services.` },
    { tool: "service_detect", label: "Service-detect open ports", prompt: (v) => `Service-detect and fingerprint open ports discovered under ${v}.` },
  ],
  subdomain: [
    { tool: "portscan", label: "Port-scan host", prompt: (v) => `Run port discovery and service detection against ${v}.` },
    { tool: "service_detect", label: "Service-detect host", prompt: (v) => `Fingerprint open services on ${v}.` },
  ],
  host: [
    { tool: "portscan", label: "Port-scan host", prompt: (v) => `Run port discovery and service detection against ${v}.` },
    { tool: "service_detect", label: "Service-detect host", prompt: (v) => `Fingerprint open services on ${v}.` },
  ],
  ip: [
    { tool: "portscan", label: "Port-scan IP", prompt: (v) => `Run port discovery and service detection against ${v}.` },
    { tool: "service_detect", label: "Service-detect IP", prompt: (v) => `Fingerprint open services on ${v}.` },
    { tool: "reverse_dns", label: "Reverse DNS", prompt: (v) => `Run reverse DNS lookup for ${v}.` },
  ],
  cidr: [
    { tool: "subnet_sweep", label: "Sweep CIDR", prompt: (v) => `Discover live hosts in ${v} and enumerate open ports across the range.` },
  ],
  url: [
    { tool: "httpx_probe", label: "Probe URL", prompt: (v) => `Probe ${v}: fingerprint status, title, redirects, and notable headers.` },
  ],
  email: [
    { tool: null, label: "Investigate email", prompt: (v) => `Investigate ${v}: pivot on accounts, breach records, and exposed credentials.` },
  ],
};

export function EntityWorkbenchPage() {
  const params = useSearchParams();
  const slug = params.get("slug") ?? "";
  const type = params.get("type") ?? "";
  const value = params.get("value") ?? "";
  const requestedTab = params.get("tab") as Tab | null;
  const [tab, setTab] = useState<Tab>(
    requestedTab && TABS.some((item) => item.id === requestedTab)
      ? requestedTab
      : "overview",
  );
  const [entities, setEntities] = useState<Entity[] | null>(null);
  const [stored, setStored] = useState<StoredEntity[] | null>(null);
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [scope, setScope] = useState<ScopeItem[] | null>(null);
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    if (!slug) return;
    setFetchError(null);
    let active = true;
    Promise.allSettled([
      listEntities(slug),
      listStoredEntities(slug),
      listFindings(slug),
      listScope(slug),
      listTasks(slug),
    ]).then(([e, s, f, sc, t]) => {
      if (!active) return;
      const errs: string[] = [];
      setEntities(pick(e, errs, "entities"));
      setStored(pick(s, errs, "imports"));
      setFindings(pick(f, errs, "findings"));
      setScope(pick(sc, errs, "scope"));
      setTasks(pick(t, errs, "tasks"));
      setFetchError(errs.length ? `Failed to load: ${errs.join(", ")}` : null);
    });
    return () => {
      active = false;
    };
  }, [slug, refreshKey]);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  const entity = useMemo(
    () => (entities ?? []).find((e) => e.type === type && e.value === value) ?? null,
    [entities, type, value],
  );
  const storedMatches = useMemo(
    () => (stored ?? []).filter((e) => e.type === type && e.value === value),
    [stored, type, value],
  );
  const relatedFindings = useMemo(
    () => relatedForEntity(value, entity, storedMatches, findings ?? []),
    [entity, findings, storedMatches, value],
  );
  const relatedTasks = useMemo(
    () => (tasks ?? []).filter((t) => taskTouchesEntity(t, value)),
    [tasks, value],
  );
  const scopeMatches = useMemo(
    () => (scope ?? []).filter((s) => scopeMatchesValue(s, value)),
    [scope, value],
  );
  const actionCount = (ACTIONS[type] ?? []).length;

  if (!slug || !type || !value) {
    return <p className="px-6 py-10 text-sm text-destructive">Missing entity route parameters.</p>;
  }

  const loading = entities === null || findings === null;

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <div className="mb-4 flex items-center justify-between gap-3">
        <Link href={`/e?slug=${encodeURIComponent(slug)}&view=entities`} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-3.5 w-3.5" /> back to entities
        </Link>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] text-muted-foreground">{type}:{value}</span>
          <button
            type="button"
            onClick={refresh}
            className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <RefreshCcw className="h-3 w-3" /> Refresh
          </button>
        </div>
      </div>

      <header className="rounded-lg border border-border bg-card p-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{TYPE_LABEL[type] ?? type}</Badge>
          {entity && <Badge variant="outline" className={SEVERITY_CLASS[entity.severity]}>{entity.severity}</Badge>}
          <Badge variant="secondary" className="text-[10px]">{entity?.count ?? 0} finding refs</Badge>
          {storedMatches.length > 0 && <Badge variant="outline" className="text-[10px]">{storedMatches.length} imported</Badge>}
        </div>
        <h1 className="mt-3 break-all font-mono text-2xl font-semibold leading-tight">{value}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Entity workbench: provenance, related findings, scope status, and next actions.
        </p>
      </header>

      <section className="mt-5 overflow-hidden rounded-lg border border-border bg-card/40">
        <div className="border-b border-border bg-background/60 px-4 py-3">
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted/50 p-1 text-xs sm:flex">
            {TABS.map((item) => (
              <button key={item.id} type="button" onClick={() => setTab(item.id)} className={cn("rounded-md px-3 py-1.5 font-medium transition-colors", tab === item.id ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
                {item.label}
                {item.id === "tools" && actionCount > 0 && <span className="ml-1 rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] text-black">{actionCount}</span>}
              </button>
            ))}
          </div>
        </div>

        <div className="p-4">
          {fetchError && (
            <p className="mb-3 rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-700 dark:text-rose-200">
              {fetchError}
            </p>
          )}
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading entity context…</p>
          ) : tab === "overview" ? (
            <OverviewPanel entity={entity} value={value} scopeMatches={scopeMatches} storedMatches={storedMatches} relatedFindings={relatedFindings} relatedTasks={relatedTasks} slug={slug} />
          ) : tab === "findings" ? (
            <FindingsPanel findings={relatedFindings} slug={slug} />
          ) : tab === "tools" ? (
            <ToolsPanel type={type} value={value} tasks={relatedTasks} slug={slug} />
          ) : tab === "evidence" ? (
            <EvidencePanel storedMatches={storedMatches} entity={entity} slug={slug} />
          ) : (
            <ActivityPanel entity={entity} findings={relatedFindings} tasks={relatedTasks} storedMatches={storedMatches} slug={slug} />
          )}
        </div>
      </section>
    </div>
  );
}

function OverviewPanel({ entity, value, scopeMatches, storedMatches, relatedFindings, relatedTasks, slug }: { entity: Entity | null; value: string; scopeMatches: ScopeItem[]; storedMatches: StoredEntity[]; relatedFindings: Finding[]; relatedTasks: Task[]; slug: string }) {
  const scopeState = scopeMatches.some((s) => s.is_exclusion) ? "excluded" : scopeMatches.some((s) => s.source === "found") ? "found scope" : scopeMatches.length ? "declared scope" : "unknown";
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Metric label="Scope / ROE" value={scopeState} tone={scopeState === "unknown" ? "warn" : scopeState === "excluded" ? "bad" : "good"} icon={<ShieldCheck className="h-4 w-4" />} />
      <Metric label="Related findings" value={String(relatedFindings.length || entity?.count || 0)} icon={<Boxes className="h-4 w-4" />} />
      <Metric label="Tool actions/runs" value={String(relatedTasks.length)} />
      <section className="rounded-lg border border-border bg-background p-4 lg:col-span-3">
        <h2 className="text-sm font-medium">Entity summary</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          <span className="font-mono text-foreground">{value}</span> appears in {entity?.count ?? relatedFindings.length} finding reference(s) and {storedMatches.length} imported record(s).
        </p>
        {scopeMatches.length > 0 && <ul className="mt-3 space-y-2 text-xs">{scopeMatches.map((s) => <li key={s.id}><Link href={`/e?slug=${encodeURIComponent(slug)}&view=scope`} className="block rounded border border-border p-2 hover:border-foreground/30 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">Matched scope <span className="font-mono">{s.kind}:{s.value}</span> · {s.is_exclusion ? "exclusion" : s.source ?? "defined"}</Link></li>)}</ul>}
      </section>
    </div>
  );
}

function FindingsPanel({ findings, slug }: { findings: Finding[]; slug: string }) {
  if (findings.length === 0) return <p className="text-sm text-muted-foreground">No related findings.</p>;
  return <ul className="space-y-2">{findings.map((f) => <li key={f.id} className="rounded-md border border-border bg-background p-3"><div className="flex flex-wrap items-center justify-between gap-2"><Link className="rounded-sm font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" href={`/e/findings/${f.id}?slug=${encodeURIComponent(slug)}`}>{f.title}</Link><Badge variant="outline" className={SEVERITY_CLASS[f.severity]}>{f.severity}</Badge></div><p className="mt-1 text-xs text-muted-foreground">{f.status} · {f.phase} · {f.target ?? "no target"}</p><Link className="mt-2 inline-block text-xs text-muted-foreground hover:text-foreground" href={`/e/findings/${f.id}?slug=${encodeURIComponent(slug)}`}>Open finding →</Link></li>)}</ul>;
}

function ToolsPanel({ type, value, tasks, slug }: { type: string; value: string; tasks: Task[]; slug: string }) {
  const [copied, setCopied] = useState<string | null>(null);
  const actions = ACTIONS[type] ?? [];
  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-amber-400/40 bg-amber-400/10 p-4">
        <h2 className="text-sm font-medium">Recommended actions</h2>
        <p className="mt-1 text-xs text-muted-foreground">Copy a prompt into the engagement runner, or use it as input for the finding/entity AI workflow.</p>
        {actions.length === 0 ? <p className="mt-3 text-xs text-muted-foreground">No action chain defined for this entity type.</p> : <div className="mt-3 space-y-2">{actions.map((a) => { const prompt = a.prompt(value); return <div key={a.label} className="rounded-md border border-amber-400/30 bg-background p-3"><div className="flex items-start justify-between gap-2"><div><p className="text-sm font-medium">{a.label}</p><p className="mt-1 font-mono text-xs text-muted-foreground">{a.tool ?? "manual investigation"}</p><p className="mt-2 text-xs text-muted-foreground">{prompt}</p></div><button type="button" className="rounded border border-border px-2 py-1 text-xs" onClick={() => { void navigator.clipboard.writeText(prompt); setCopied(a.label); }}><Clipboard className="mr-1 inline h-3 w-3" />Copy</button></div>{copied === a.label && <p className="mt-1 text-[10px] text-emerald-600">Copied</p>}</div>; })}</div>}
      </section>
      <ActionHistory tasks={tasks} slug={slug} />
    </div>
  );
}

function EvidencePanel({ storedMatches, entity, slug }: { storedMatches: StoredEntity[]; entity: Entity | null; slug: string }) {
  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-border bg-background p-4">
        <h2 className="text-sm font-medium">Imported records</h2>
        {storedMatches.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">No imported records for this exact entity.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {storedMatches.map((stored) => (
              <li key={stored.id} className="rounded border border-border p-3 text-xs">
                <p className="font-mono">{stored.type}:{stored.value}</p>
                <p className="mt-1 text-muted-foreground">
                  {stored.finding_refs.length > 0 ? (
                    <>
                      Promoted from{" "}
                      {stored.finding_refs.map((finding, index) => (
                        <span key={finding.id}>
                          {index > 0 && ", "}
                          <Link
                            href={`/e/findings/${finding.id}?slug=${encodeURIComponent(slug)}`}
                            className="font-medium text-foreground hover:underline"
                          >
                            {finding.title}
                          </Link>
                        </span>
                      ))}
                    </>
                  ) : (
                    stored.source_attribution ?? stored.source_tool
                  )}{" "}· <DateTime value={stored.created_at} />
                </p>
                <pre className="mt-2 max-h-44 overflow-auto rounded bg-muted/40 p-2">{JSON.stringify(stored.properties, null, 2)}</pre>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="rounded-lg border border-border bg-background p-4">
        <h2 className="text-sm font-medium">Finding provenance</h2>
        {entity && entity.findings.length > 0 ? (
          <ul className="mt-3 space-y-2">
            {entity.findings.map((finding) => (
              <li key={finding.id}>
                <Link
                  href={`/e/findings/${finding.id}?slug=${encodeURIComponent(slug)}`}
                  className="block rounded border border-border p-2 text-xs hover:border-foreground/30 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="font-medium">{finding.title}</span> · {finding.tool ?? "manual"} · {finding.phase}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">No derived finding provenance.</p>
        )}
      </section>
    </div>
  );
}

function ActivityPanel({ entity, findings, tasks, storedMatches, slug }: { entity: Entity | null; findings: Finding[]; tasks: Task[]; storedMatches: StoredEntity[]; slug: string }) {
  const rows = [
    ...findings.map((finding) => ({
      ts: finding.created_at,
      label: `Finding: ${finding.title}`,
      detail: `${finding.severity} · ${finding.status}`,
      href: `/e/findings/${finding.id}?slug=${encodeURIComponent(slug)}`,
    })),
    ...tasks.map((task) => ({
      ts: task.dispatched_at ?? task.created_at,
      label: `Tool action: ${task.title}`,
      detail: `${task.status} · ${String(task.payload.tool ?? "?")}`,
      href: `/e?slug=${encodeURIComponent(slug)}&view=status&run=${encodeURIComponent(task.id)}`,
    })),
    ...storedMatches.map((stored) => ({
      ts: stored.created_at,
      label: `Imported from ${stored.source_tool}`,
      detail: stored.source_attribution ?? stored.type,
      href: null,
    })),
  ].sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
  if (!entity && rows.length === 0) return <p className="text-sm text-muted-foreground">No activity for this entity yet.</p>;
  return (
    <ol className="space-y-3 border-l border-border pl-4">
      {rows.map((row, index) => (
        <li key={`${row.ts}-${index}`} className="relative">
          <span className="absolute -left-[1.4rem] flex h-5 w-5 items-center justify-center rounded-full bg-card"><Network className="h-3.5 w-3.5 text-muted-foreground" /></span>
          {row.href ? (
            <Link href={row.href} className="rounded-sm text-sm font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{row.label}</Link>
          ) : (
            <p className="text-sm font-medium">{row.label}</p>
          )}
          <p className="text-xs text-muted-foreground"><DateTime value={row.ts} /> · {row.detail}</p>
        </li>
      ))}
    </ol>
  );
}

function Metric({ label, value, tone, icon }: { label: string; value: string; tone?: "good" | "bad" | "warn"; icon?: ReactNode }) {
  return <div className="rounded-lg border border-border bg-background p-4"><div className="flex items-center gap-1.5"><span className="text-muted-foreground">{icon}</span><p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p></div><p className={cn("mt-2 text-lg font-semibold", tone === "good" && "text-emerald-600", tone === "bad" && "text-rose-600", tone === "warn" && "text-amber-600")}>{value}</p></div>;
}

function ActionHistory({ tasks, slug }: { tasks: Task[]; slug: string }) {
  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <h2 className="text-sm font-medium">Tool action history</h2>
      {tasks.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">No matching task history.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {tasks.map((task) => (
            <li key={task.id} className="rounded border border-border bg-background p-3 text-xs">
              <div className="flex justify-between gap-2">
                <Link
                  href={`/e?slug=${encodeURIComponent(slug)}&view=status&run=${encodeURIComponent(task.id)}`}
                  className="rounded-sm font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {task.title}
                </Link>
                <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase text-muted-foreground">{task.status}</span>
              </div>
              <p className="mt-1 font-mono text-muted-foreground">{String(task.payload.tool ?? "?")} → {String(task.payload.target ?? "?")}</p>
              <p className="mt-1 text-muted-foreground">run: {task.run_id ?? "not dispatched"}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function pick<T>(
  r: PromiseSettledResult<T[]>,
  errs: string[],
  label: string,
): T[] {
  if (r.status === "fulfilled") return r.value;
  errs.push(label);
  return [];
}

function relatedForEntity(value: string, entity: Entity | null, storedMatches: StoredEntity[], findings: Finding[]) {
  const ids = new Set([
    ...(entity?.findings ?? []).map((finding) => finding.id),
    ...storedMatches.flatMap((stored) => stored.finding_refs.map((finding) => finding.id)),
  ]);
  const lower = value.toLowerCase();
  return findings.filter((f) => ids.has(f.id) || JSON.stringify({ target: f.target, title: f.title, summary: f.summary, data: f.data }).toLowerCase().includes(lower));
}

function taskTouchesEntity(task: Task, value: string): boolean {
  const lower = value.toLowerCase();
  return JSON.stringify(task.payload).toLowerCase().includes(lower) || task.title.toLowerCase().includes(lower);
}

function scopeMatchesValue(scope: ScopeItem, value: string): boolean {
  const a = scope.value.toLowerCase();
  const b = value.toLowerCase();
  return a === b || a.includes(b) || b.includes(a);
}
