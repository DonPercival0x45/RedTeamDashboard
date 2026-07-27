"use client";

import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Layers3,
  Plus,
  Search,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { KeyboardEvent } from "react";

import { createFindingFromHierarchyItem } from "@/lib/api";
import {
  FINDING_WORKSPACE_VIEWS,
  filterHierarchy,
} from "@/lib/finding-hierarchy";
import { qk, useFindingHierarchy } from "@/lib/hooks";
import type {
  Finding,
  FindingDuplicateCandidate,
  FindingFromHierarchyItemCreate,
  FindingHierarchyItem,
  FindingPhase,
  FindingWorkspaceView,
  Severity,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "border-rose-500/50 bg-rose-500/10 text-rose-700 dark:text-rose-200",
  high: "border-pink-500/50 bg-pink-500/10 text-pink-700 dark:text-pink-200",
  medium: "border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-200",
  low: "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
  info: "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-200",
};

function StatusCounts({ item }: { item: FindingHierarchyItem }) {
  const { rollup } = item;
  return (
    <div className="flex flex-wrap gap-1 text-[10px]">
      {rollup.needs_review > 0 && (
        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-800 dark:text-amber-200">
          {rollup.needs_review} review
        </span>
      )}
      {rollup.actionable > 0 && (
        <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-rose-800 dark:text-rose-200">
          {rollup.actionable} actionable
        </span>
      )}
      {rollup.inventory > 0 && (
        <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-sky-800 dark:text-sky-200">
          {rollup.inventory} inventory
        </span>
      )}
      {rollup.resolved_excluded > 0 && (
        <span className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
          {rollup.resolved_excluded} resolved/excluded
        </span>
      )}
    </div>
  );
}

function HierarchyRow({
  item,
  depth,
  expanded,
  onToggle,
  onSelect,
  onCreate,
  canWrite,
}: {
  item: FindingHierarchyItem;
  depth: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onSelect: (item: FindingHierarchyItem) => void;
  onCreate: (item: FindingHierarchyItem) => void;
  canWrite: boolean;
}) {
  const hasChildren = item.children.length > 0;
  const isExpanded = expanded.has(item.id);
  return (
    <>
      <div
        role="treeitem"
        aria-selected={false}
        aria-expanded={hasChildren ? isExpanded : undefined}
        className="group flex items-start gap-2 border-b border-border/60 px-3 py-2.5 last:border-0 hover:bg-secondary/30"
        style={{ paddingLeft: `${12 + depth * 22}px` }}
      >
        <button
          type="button"
          aria-label={`${isExpanded ? "Collapse" : "Expand"} ${item.label}`}
          disabled={!hasChildren}
          onClick={() => hasChildren && onToggle(item.id)}
          className="mt-0.5 rounded p-0.5 text-muted-foreground disabled:invisible"
        >
          {isExpanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>
        <button
          type="button"
          onClick={() => onSelect(item)}
          className="min-w-0 flex-1 text-left"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn("font-medium", depth === 0 ? "text-sm" : "text-xs")}>{item.label}</span>
            <Badge variant="outline" className={cn("text-[10px]", SEVERITY_CLASS[item.rollup.max_severity])}>
              {item.rollup.max_severity}
            </Badge>
            {item.kind !== "finding" && (
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                {item.kind.replace("_", " ")}
              </span>
            )}
          </div>
          <div className="mt-1"><StatusCounts item={item} /></div>
        </button>
        {canWrite && item.create_finding_allowed && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 shrink-0 px-2 text-xs opacity-70 group-hover:opacity-100"
            onClick={() => onCreate(item)}
          >
            <Plus className="mr-1 h-3 w-3" /> Create Finding
          </Button>
        )}
      </div>
      {hasChildren && isExpanded && (
        <div role="group">
          {item.children.map((child) => (
            <HierarchyRow
              key={child.id}
              item={child}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
              onSelect={onSelect}
              onCreate={onCreate}
              canWrite={canWrite}
            />
          ))}
        </div>
      )}
    </>
  );
}

function ItemDetail({
  item,
  slug,
  onClose,
  onCreate,
  canWrite,
  view,
}: {
  item: FindingHierarchyItem;
  slug: string;
  onClose: () => void;
  onCreate: (item: FindingHierarchyItem) => void;
  canWrite: boolean;
  view: FindingWorkspaceView;
}) {
  const returnTo = `/e?slug=${encodeURIComponent(slug)}&view=findings&findingView=${view}`;
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{item.label}</DialogTitle>
          <DialogDescription>
            {item.kind.replace("_", " ")} · {item.rollup.distinct_findings} source Finding reference{item.rollup.distinct_findings === 1 ? "" : "s"}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className={SEVERITY_CLASS[item.rollup.max_severity]}>
            {item.rollup.max_severity}
          </Badge>
          <StatusCounts item={item} />
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          {item.ip && <><dt className="text-muted-foreground">IP</dt><dd className="font-mono">{item.ip}</dd></>}
          {item.hostname && <><dt className="text-muted-foreground">Hostname</dt><dd className="font-mono">{item.hostname}</dd></>}
          {item.port && <><dt className="text-muted-foreground">Service</dt><dd className="font-mono">{item.port}/{item.protocol ?? "unknown"}{item.service ? ` · ${item.service}` : ""}</dd></>}
          {item.url && <><dt className="text-muted-foreground">URL</dt><dd className="break-all font-mono">{item.url}</dd></>}
        </dl>
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Supporting Findings</h3>
          <div className="space-y-2">
            {item.finding_refs.length === 0 ? (
              <p className="text-xs text-muted-foreground">Supporting Findings are attached to nested items.</p>
            ) : item.finding_refs.map((ref) => (
              <Link
                key={ref.id}
                href={`/e/findings/${ref.id}?slug=${encodeURIComponent(slug)}&returnTo=${encodeURIComponent(returnTo)}`}
                className="flex items-start justify-between gap-3 rounded-md border border-border p-2 text-xs hover:bg-muted"
              >
                <span>
                  <span className="font-medium">{ref.title}</span>
                  <span className="mt-0.5 block text-muted-foreground">{ref.tool ?? "manual"} · {ref.status.replaceAll("_", " ")}</span>
                </span>
                <ExternalLink className="h-3.5 w-3.5 shrink-0" />
              </Link>
            ))}
          </div>
        </section>
        <DialogFooter>
          {canWrite && (
            <Button type="button" onClick={() => { onClose(); onCreate(item); }}>
              <Plus className="mr-1.5 h-4 w-4" /> Create Finding
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CreateFindingFromItemDialog({
  slug,
  item,
  onClose,
  onCreated,
  view,
}: {
  slug: string;
  item: FindingHierarchyItem;
  onClose: () => void;
  onCreated: (finding: Finding) => void;
  view: FindingWorkspaceView;
}) {
  const qc = useQueryClient();
  const returnTo = `/e?slug=${encodeURIComponent(slug)}&view=findings&findingView=${view}`;
  const [title, setTitle] = useState(item.suggested_title ?? item.label);
  const [summary, setSummary] = useState("");
  const target = item.suggested_target ?? item.value ?? "";
  const [severity, setSeverity] = useState<Severity>(item.rollup.max_severity);
  const [phase, setPhase] = useState<FindingPhase>("general");
  const [candidates, setCandidates] = useState<FindingDuplicateCandidate[]>([]);
  const idempotencyKey = useMemo(
    () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-0000-4000-8000-000000000000`,
    [],
  );
  const mutation = useMutation({
    mutationFn: (duplicateDecision: "review" | "create_anyway") => {
      const body: FindingFromHierarchyItemCreate = {
        item_id: item.id,
        title,
        summary: summary || null,
        severity,
        phase,
        target: target || null,
        observed_at: item.rollup.latest_at,
        duplicate_decision: duplicateDecision,
        reviewed_duplicate_ids: candidates.map((candidate) => candidate.id),
        idempotency_key: idempotencyKey,
      };
      return createFindingFromHierarchyItem(slug, body);
    },
    onSuccess: (response) => {
      if (response.state === "duplicate_warning") {
        setCandidates(response.candidates);
        return;
      }
      onCreated(response.finding);
      void qc.invalidateQueries({ queryKey: qk.findings(slug) });
      void qc.invalidateQueries({ queryKey: qk.findingHierarchy(slug) });
      void qc.invalidateQueries({ queryKey: qk.entities(slug) });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Create Finding from {item.label}</DialogTitle>
          <DialogDescription>
            The source item and supporting Finding IDs remain attached as immutable creation context. This does not change scope.
          </DialogDescription>
        </DialogHeader>
        <label className="space-y-1 text-xs"><span>Title</span><Input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <label className="space-y-1 text-xs"><span>Affected target</span><Input value={target} readOnly aria-readonly="true" aria-label="Affected target" /><span className="block text-muted-foreground">Bound to the selected inventory item so its provenance cannot drift.</span></label>
        <label className="space-y-1 text-xs"><span>Summary / rationale</span><textarea value={summary} onChange={(event) => setSummary(event.target.value)} rows={4} className="w-full rounded-md border border-input bg-background px-3 py-2" /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1 text-xs"><span>Severity</span><select value={severity} onChange={(event) => setSeverity(event.target.value as Severity)} className="h-9 w-full rounded-md border border-input bg-background px-2">{(["info", "low", "medium", "high", "critical"] as Severity[]).map((value) => <option key={value}>{value}</option>)}</select></label>
          <label className="space-y-1 text-xs"><span>Phase</span><select value={phase} onChange={(event) => setPhase(event.target.value as FindingPhase)} className="h-9 w-full rounded-md border border-input bg-background px-2">{(["general", "osint", "vuln_scan", "exploit", "phishing"] as FindingPhase[]).map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
        </div>
        {candidates.length > 0 && (
          <div className="space-y-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-xs">
            <p className="font-medium">Possible existing Findings</p>
            {candidates.map((candidate) => (
              <Link key={candidate.id} href={`/e/findings/${candidate.id}?slug=${encodeURIComponent(slug)}&returnTo=${encodeURIComponent(returnTo)}`} className="flex justify-between gap-2 rounded border border-amber-500/30 p-2 hover:bg-amber-500/10">
                <span>{candidate.title}<span className="block text-muted-foreground">{candidate.match_reason}</span></span><ExternalLink className="h-3.5 w-3.5" />
              </Link>
            ))}
            <p>Open an existing Finding, cancel, or explicitly create another.</p>
          </div>
        )}
        {mutation.error && <p className="text-xs text-destructive">{mutation.error instanceof Error ? mutation.error.message : String(mutation.error)}</p>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          {candidates.length > 0 ? (
            <Button type="button" disabled={mutation.isPending || !title.trim()} onClick={() => mutation.mutate("create_anyway")}>Create anyway</Button>
          ) : (
            <Button type="button" disabled={mutation.isPending || !title.trim()} onClick={() => mutation.mutate("review")}>{mutation.isPending ? "Checking…" : "Create Finding"}</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function FindingHierarchyWorkspace({
  slug,
  canWrite,
  view,
  onViewChange,
  onCreated,
  onUseClassic,
}: {
  slug: string;
  canWrite: boolean;
  view: FindingWorkspaceView;
  onViewChange: (view: FindingWorkspaceView) => void;
  onCreated: (finding: Finding) => void;
  onUseClassic: () => void;
}) {
  const query = useFindingHierarchy(slug);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<FindingHierarchyItem | null>(null);
  const [creating, setCreating] = useState<FindingHierarchyItem | null>(null);
  const visible = useMemo(
    () =>
      filterHierarchy(
        query.data ? [...query.data.assets, ...query.data.ungrouped] : [],
        view,
        search,
      ),
    [query.data, search, view],
  );
  const moveTabFocus = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = Array.from(
      event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
        '[role="tab"]',
      ) ?? [],
    );
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    tabs[next]?.focus();
    tabs[next]?.click();
  };
  const toggle = (id: string) => setExpanded((previous) => {
    const next = new Set(previous);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h2 className="flex items-center gap-2 text-base font-semibold"><Layers3 className="h-4 w-4" /> Entity-centred Findings</h2><p className="text-xs text-muted-foreground">Prioritized IP and domain bundles. Source Findings, evidence, and lifecycle remain independent.</p></div>
        {canWrite && <Button type="button" size="sm" variant="outline" onClick={onUseClassic}>Classic table</Button>}
      </div>
      <div role="tablist" aria-label="Finding workspace views" className="flex flex-wrap gap-1 rounded-lg border border-border bg-muted/30 p-1">
        {FINDING_WORKSPACE_VIEWS.map((option, index) => (
          <button key={option.value} type="button" role="tab" aria-selected={view === option.value} aria-controls="finding-workspace-panel" tabIndex={view === option.value ? 0 : -1} onKeyDown={(event) => moveTabFocus(event, index)} onClick={() => onViewChange(option.value)} className={cn("rounded-md px-3 py-1.5 text-xs", view === option.value ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:text-foreground")}>
            {option.label}{query.data ? ` (${query.data.counts[option.value]})` : ""}
          </button>
        ))}
      </div>
      <div id="finding-workspace-panel" role="tabpanel" className="space-y-4">
        <div className="relative"><Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="Search entity-centred findings" placeholder="Search IPs, domains, services, ports, tools, or Finding IDs" value={search} onChange={(event) => setSearch(event.target.value)} className="pl-8" /></div>
        {query.isLoading ? <p className="text-sm text-muted-foreground">Building the Findings hierarchy…</p> : query.error && !query.data ? <div className="rounded-md border border-destructive/40 p-4 text-sm"><p>Could not build the Findings hierarchy. The classic table remains available.</p><Button size="sm" variant="outline" className="mt-2" onClick={() => void query.refetch()}>Retry</Button></div> : visible.length === 0 ? <p className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">No items match this view.</p> : <div role="tree" className="max-h-[68vh] overflow-auto rounded-lg border border-border">{visible.map((item) => <HierarchyRow key={item.id} item={item} depth={0} expanded={expanded} onToggle={toggle} onSelect={setSelected} onCreate={setCreating} canWrite={canWrite} />)}</div>}
      </div>
      {selected && <ItemDetail item={selected} slug={slug} onClose={() => setSelected(null)} onCreate={setCreating} canWrite={canWrite} view={view} />}
      {creating && <CreateFindingFromItemDialog slug={slug} item={creating} onClose={() => setCreating(null)} onCreated={onCreated} view={view} />}
    </div>
  );
}
