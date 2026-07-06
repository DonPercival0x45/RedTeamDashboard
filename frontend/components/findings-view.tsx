"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Ban, Layers, Plus, Search, Sparkles, Trash2, Upload, Wand2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  acceptSuggestion,
  analyzeFinding,
  bulkDeleteFindings,
  correlateFindings,
  createFinding,
  createFindingSummary,
  deleteAttachment,
  deleteFinding,
  dismissSuggestion,
  listAttachments,
  listFindingSummaries,
  loadAttachmentBlob,
  mergeFindings,
  regroupFindingsApply,
  regroupFindingsPreview,
  triageFinding,
  updateFinding,
  uploadAttachment,
  validateFinding,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { FindingImporter } from "@/components/finding-importer";
import { BurpImporter } from "@/components/burp-importer";
import type {
  Attachment,
  CorrelateGroup,
  Finding,
  FindingExclusion,
  FindingPhase,
  FindingSort,
  FindingSummaryEntry,
  FindingValidationStatus,
  RegroupProposal,
  Severity,
  Suggestion,
} from "@/lib/types";

// ── display helpers ────────────────────────────────────────────────────────

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
  info: 0,
};

// v0.8.1: severity colour map locked per user spec.
//   critical = red   high = pink   medium = yellow   low = green   info = blue
// Used by the severity Badge in the findings table and by the
// SeverityMetricCard tiles up top.
const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "border-rose-500/50 bg-rose-500/15 text-rose-200",
  high: "border-pink-400/50 bg-pink-400/15 text-pink-200",
  medium: "border-yellow-400/50 bg-yellow-400/15 text-yellow-100",
  low: "border-emerald-500/50 bg-emerald-500/15 text-emerald-200",
  info: "border-sky-500/50 bg-sky-500/15 text-sky-200",
};

const STATUS_LABEL: Record<FindingValidationStatus, string> = {
  pending_validation: "Pending",
  validated: "Validated",
  rejected: "Rejected",
  false_positive: "False positive",
  needs_review: "Needs review",
};

const PHASE_LABEL: Record<FindingPhase, string> = {
  osint: "OSINT",
  vuln_scan: "Vuln Scan",
  exploit: "Exploit",
  phishing: "Phishing",
  general: "General",
};

// v1.4.0: analyst-set reportability marker. Distinct from FindingStatus
// so an excluded row still shows in the tab (dimmed + badge) while the
// report exporter drops it when the Report-tab toggle is on.
const EXCLUSION_LABEL: Record<FindingExclusion, string> = {
  out_of_scope: "Out of scope",
  outside_roe: "Outside ROE",
};

const EXCLUSION_BADGE_CLASS: Record<FindingExclusion, string> = {
  out_of_scope: "border-amber-500/60 bg-amber-500/15 text-amber-100",
  outside_roe: "border-orange-500/60 bg-orange-500/15 text-orange-100",
};

const SEVERITY_OPTIONS: Severity[] = ["info", "low", "medium", "high", "critical"];
const PHASE_OPTIONS: FindingPhase[] = [
  "osint",
  "vuln_scan",
  "exploit",
  "phishing",
  "general",
];

const PHASE_FILTERS: (FindingPhase | "all")[] = [
  "all",
  "osint",
  "vuln_scan",
  "exploit",
  "phishing",
];

const STATUS_FILTERS: (FindingValidationStatus | "all")[] = [
  "all",
  "pending_validation",
  "validated",
];

const SORT_LABEL: Record<FindingSort, string> = {
  newest: "Newest first",
  severity: "Severity",
  observed: "Observed date",
};

function shortId(id: string): string {
  return id.replace(/-/g, "").slice(0, 6).toUpperCase();
}

function formatShortDate(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    year: "2-digit",
    month: "short",
    day: "numeric",
  });
}

// ── component ────────────────────────────────────────────────────────────

export function FindingsView({
  slug,
  findings,
  onUpdated,
  onDeleted,
}: {
  slug: string;
  findings: Finding[];
  onUpdated: (finding: Finding) => void;
  onDeleted: (findingId: string) => void;
}) {
  const [phase, setPhase] = useState<FindingPhase | "all">("all");
  const [status, setStatus] = useState<FindingValidationStatus | "all">("all");
  const [sort, setSort] = useState<FindingSort>("newest");
  const [selected, setSelected] = useState<Finding | null>(null);
  const [showImporter, setShowImporter] = useState(false);
  const [showBurpImporter, setShowBurpImporter] = useState(false);
  // v1.4.0: manual "Add finding" modal + agent-driven Correlate modal.
  // Both are center-screen dialogs mounted below the table.
  const [showAddModal, setShowAddModal] = useState(false);
  const [showCorrelateModal, setShowCorrelateModal] = useState(false);
  // v1.4.1: deterministic auto-grouping modal. Kicked from the "Group
  // findings" button; runs compute_group_key() over every ungrouped
  // row and folds anything sharing a key.
  const [showRegroupModal, setShowRegroupModal] = useState(false);
  // v1.4.0: client-side substring search on title / summary / target /
  // short-id. Kept in the tab itself (not the URL) so hitting Findings
  // from the nav lands on a clean view; the filter panel below the
  // metrics has the input.
  const [search, setSearch] = useState("");

  // v0.10.0: multi-select for bulk delete. Set of finding IDs; two-click
  // confirm before we actually call bulk-delete.
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [confirmingBulk, setConfirmingBulk] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);

  // v0.8.1: severity-only filter driven by clicking the metric tiles.
  // "all" means no severity filter active. Pending validation has its own
  // tile that toggles the status filter to pending_validation instead.
  const [severityFilter, setSeverityFilter] = useState<Severity | "all">("all");

  const counts = {
    critical: findings.filter((f) => f.severity === "critical").length,
    high: findings.filter((f) => f.severity === "high").length,
    medium: findings.filter((f) => f.severity === "medium").length,
    low: findings.filter((f) => f.severity === "low").length,
    info: findings.filter((f) => f.severity === "info").length,
    pending: findings.filter((f) => f.status === "pending_validation").length,
  };

  const toggleSeverity = (s: Severity) =>
    setSeverityFilter((prev) => (prev === s ? "all" : s));
  const togglePending = () =>
    setStatus((prev) =>
      prev === "pending_validation" ? "all" : "pending_validation",
    );

  const compareFindings = (a: Finding, b: Finding): number => {
    switch (sort) {
      case "severity": {
        const sev = SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity];
        if (sev !== 0) return sev;
        return b.created_at.localeCompare(a.created_at);
      }
      case "observed": {
        // Nulls last, then newest first.
        const ao = a.observed_at;
        const bo = b.observed_at;
        if (ao && bo) return bo.localeCompare(ao);
        if (ao && !bo) return -1;
        if (!ao && bo) return 1;
        return b.created_at.localeCompare(a.created_at);
      }
      default:
        return b.created_at.localeCompare(a.created_at);
    }
  };

  const trimmedSearch = search.trim().toLowerCase();
  const matchesSearch = (f: Finding): boolean => {
    if (!trimmedSearch) return true;
    const short = shortId(f.id).toLowerCase();
    return (
      f.title.toLowerCase().includes(trimmedSearch) ||
      (f.summary?.toLowerCase().includes(trimmedSearch) ?? false) ||
      (f.target?.toLowerCase().includes(trimmedSearch) ?? false) ||
      short.includes(trimmedSearch)
    );
  };

  const visible = findings
    .filter((f) => phase === "all" || f.phase === phase)
    .filter((f) => status === "all" || f.status === status)
    .filter((f) => severityFilter === "all" || f.severity === severityFilter)
    .filter(matchesSearch)
    .slice()
    .sort(compareFindings);

  const handleUpdated = (f: Finding) => {
    onUpdated(f);
    setSelected(f);
  };

  const toggleChecked = (id: string) => {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setConfirmingBulk(false);
    setBulkError(null);
  };

  const clearSelection = () => {
    setCheckedIds(new Set());
    setConfirmingBulk(false);
    setBulkError(null);
  };

  const visibleIds = (arr: Finding[]) => arr.map((f) => f.id);
  const allVisibleChecked =
    visible.length > 0 && visible.every((f) => checkedIds.has(f.id));
  const someVisibleChecked =
    visible.some((f) => checkedIds.has(f.id)) && !allVisibleChecked;

  const toggleAllVisible = () => {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (allVisibleChecked) {
        visibleIds(visible).forEach((id) => next.delete(id));
      } else {
        visibleIds(visible).forEach((id) => next.add(id));
      }
      return next;
    });
    setConfirmingBulk(false);
    setBulkError(null);
  };

  const doBulkDelete = async () => {
    if (checkedIds.size === 0) return;
    if (!confirmingBulk) {
      setConfirmingBulk(true);
      return;
    }
    setBulkDeleting(true);
    setBulkError(null);
    const ids = Array.from(checkedIds);
    try {
      const res = await bulkDeleteFindings(slug, ids);
      // Remove every ID we attempted (bulk covers "already deleted" too;
      // no reason to keep stale rows around in the client).
      ids.forEach((id) => onDeleted(id));
      clearSelection();
      // If the server skipped anything, surface a short note but don't
      // block. Most of the time this is zero.
      if (res.skipped_missing || res.skipped_already_deleted) {
        setBulkError(
          `Deleted ${res.deleted}. Skipped ${res.skipped_missing} missing, ${res.skipped_already_deleted} already deleted.`,
        );
      }
    } catch (err) {
      setBulkError(err instanceof Error ? err.message : String(err));
      setConfirmingBulk(false);
    } finally {
      setBulkDeleting(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Key metrics. v0.8.1: colour-coded per severity, with each tile
          acting as a click-to-filter button. Click a tile to filter the
          findings table by that severity; click the same tile again to
          clear. The Med/Low tile splits diagonally — Medium (yellow) sits
          in the top-left and Low (green) in the bottom-right; each half
          is its own click target. Info gets its own tile (blue). Pending
          validation toggles the status filter to pending_validation. */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <SeverityMetricCard
          label="Critical"
          value={counts.critical}
          tone="critical"
          active={severityFilter === "critical"}
          onClick={() => toggleSeverity("critical")}
        />
        <SeverityMetricCard
          label="High"
          value={counts.high}
          tone="high"
          active={severityFilter === "high"}
          onClick={() => toggleSeverity("high")}
        />
        <MediumLowSplitCard
          medium={counts.medium}
          low={counts.low}
          mediumActive={severityFilter === "medium"}
          lowActive={severityFilter === "low"}
          onMediumClick={() => toggleSeverity("medium")}
          onLowClick={() => toggleSeverity("low")}
        />
        <SeverityMetricCard
          label="Info"
          value={counts.info}
          tone="info"
          active={severityFilter === "info"}
          onClick={() => toggleSeverity("info")}
        />
        <SeverityMetricCard
          label="Pending validation"
          value={counts.pending}
          tone="pending"
          active={status === "pending_validation"}
          onClick={togglePending}
        />
      </div>

      {/* Filters + sort + import toggle */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <FilterRow
          options={PHASE_FILTERS}
          value={phase}
          onChange={setPhase}
          label={(v) => (v === "all" ? "All phases" : PHASE_LABEL[v])}
        />
        <FilterRow
          options={STATUS_FILTERS}
          value={status}
          onChange={setStatus}
          label={(v) =>
            v === "all" ? "All status" : STATUS_LABEL[v as FindingValidationStatus]
          }
        />
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="hidden sm:inline">Sort by</span>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as FindingSort)}
            className="rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          >
            {(Object.keys(SORT_LABEL) as FindingSort[]).map((opt) => (
              <option key={opt} value={opt}>
                {SORT_LABEL[opt]}
              </option>
            ))}
          </select>
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          <Button size="sm" onClick={() => setShowAddModal(true)}>
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            Add finding
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowRegroupModal(true)}
            title="Deterministic auto-grouping: fold ungrouped rows into one row per (tool × category) using the v1.4.0 vocab. No LLM cost."
          >
            <Wand2 className="mr-1.5 h-3.5 w-3.5" />
            Group findings
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowCorrelateModal(true)}
            title="Ask the CorrelateAgent to suggest which open findings describe the same underlying issue"
          >
            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
            Correlate
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setShowImporter((v) => !v);
              if (!showImporter) setShowBurpImporter(false);
            }}
          >
            <Upload className="mr-1.5 h-3.5 w-3.5" />
            {showImporter ? "Close import" : "Import"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setShowBurpImporter((v) => !v);
              if (!showBurpImporter) setShowImporter(false);
            }}
          >
            <Upload className="mr-1.5 h-3.5 w-3.5" />
            {showBurpImporter ? "Close Burp" : "Import Burp XML"}
          </Button>
        </div>
      </div>

      {/* v1.4.0: search bar. Substring match against title, summary,
          target, and the row's short-id (as shown in the ID column). */}
      <div className="relative max-w-md">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="search"
          placeholder="Search findings — title, summary, target, ID"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-8"
        />
      </div>

      {/* Inline importer panel */}
      {showImporter && (
        <FindingImporter
          slug={slug}
          onImported={(newFindings) => {
            newFindings.forEach((f) => onUpdated(f));
            setShowImporter(false);
          }}
        />
      )}
      {showBurpImporter && (
        <BurpImporter
          slug={slug}
          onImported={(newFindings) => {
            newFindings.forEach((f) => onUpdated(f));
            setShowBurpImporter(false);
          }}
        />
      )}

      {/* v0.10.0 bulk-delete action bar. Only rendered when at least one
          row is checked; sticks between filters and the table. */}
      {checkedIds.size > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-rose-500/40 bg-rose-500/5 px-3 py-2">
          <div className="flex items-center gap-3 text-sm">
            <span>
              <span className="font-medium">{checkedIds.size}</span>{" "}
              selected
            </span>
            <button
              type="button"
              onClick={clearSelection}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              Clear
            </button>
            {bulkError && (
              <span className="text-xs text-muted-foreground">{bulkError}</span>
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={bulkDeleting}
            onClick={doBulkDelete}
            className={
              confirmingBulk
                ? "border-rose-500 bg-rose-500/15 text-rose-100 hover:bg-rose-500/25"
                : "border-rose-500/50 text-rose-200 hover:bg-rose-500/10"
            }
          >
            <Trash2 className="mr-1.5 h-3.5 w-3.5" />
            {bulkDeleting
              ? "Deleting…"
              : confirmingBulk
                ? `Confirm delete ${checkedIds.size}`
                : `Delete ${checkedIds.size} finding${checkedIds.size === 1 ? "" : "s"}`}
          </Button>
        </div>
      )}

      {/* Table */}
      {visible.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No findings{findings.length ? " match these filters." : " yet."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th className="px-3 py-2 w-8">
                  <input
                    type="checkbox"
                    aria-label={
                      allVisibleChecked
                        ? "Deselect all visible findings"
                        : "Select all visible findings"
                    }
                    checked={allVisibleChecked}
                    ref={(el) => {
                      if (el) el.indeterminate = someVisibleChecked;
                    }}
                    onChange={toggleAllVisible}
                    className="cursor-pointer accent-rose-500"
                  />
                </th>
                <th className="px-3 py-2 w-20">ID</th>
                <th className="px-3 py-2">Finding</th>
                <th className="px-3 py-2">Detail</th>
                <th className="px-3 py-2 w-28">Dates</th>
                <th className="px-3 py-2 w-28">Status</th>
                <th className="px-3 py-2 w-24">Severity</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((f) => (
                <tr
                  key={f.id}
                  onClick={() => setSelected(f)}
                  className={cn(
                    "cursor-pointer border-b border-border/60 align-top last:border-0 hover:bg-secondary/40",
                    checkedIds.has(f.id) && "bg-secondary/30",
                    // v1.4.0: dim excluded rows so the analyst sees at a
                    // glance which rows the report exporter will drop.
                    f.exclusion && "opacity-60",
                  )}
                >
                  <td
                    className="px-3 py-2.5"
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleChecked(f.id);
                    }}
                  >
                    <input
                      type="checkbox"
                      aria-label={`Select ${f.title}`}
                      checked={checkedIds.has(f.id)}
                      onChange={() => toggleChecked(f.id)}
                      onClick={(e) => e.stopPropagation()}
                      className="cursor-pointer accent-rose-500"
                    />
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs text-muted-foreground">
                    {shortId(f.id)}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">
                        {f.title}
                        {typeof f.item_count === "number" && f.item_count > 1 && (
                          <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                            ({f.item_count})
                          </span>
                        )}
                      </span>
                      {f.tool === "import" && (
                        <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          imported
                        </span>
                      )}
                      {f.tool === "manual" && (
                        <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          manual
                        </span>
                      )}
                      {f.group_key && (
                        <span
                          className="rounded border border-sky-500/40 bg-sky-500/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-sky-200"
                          title={`Grouped: ${f.group_key}`}
                        >
                          <Layers className="mr-1 inline h-3 w-3" />
                          grouped
                        </span>
                      )}
                      {f.exclusion && (
                        <Badge
                          variant="outline"
                          className={cn(
                            "text-[10px] uppercase tracking-wide",
                            EXCLUSION_BADGE_CLASS[f.exclusion],
                          )}
                        >
                          <Ban className="mr-1 h-3 w-3" />
                          {EXCLUSION_LABEL[f.exclusion]}
                        </Badge>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {PHASE_LABEL[f.phase]}
                      {f.tool && f.tool !== "import" && f.tool !== "manual"
                        ? ` · ${f.tool}`
                        : ""}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs text-muted-foreground">
                    {f.target ?? "—"}
                  </td>
                  <td className="px-3 py-2.5 text-xs text-muted-foreground">
                    <div title={`Created ${new Date(f.created_at).toLocaleString()}`}>
                      <span className="text-muted-foreground/60">+</span>{" "}
                      {formatShortDate(f.created_at)}
                    </div>
                    {f.observed_at && (
                      <div
                        title={`Observed ${new Date(f.observed_at).toLocaleString()}`}
                        className="text-muted-foreground/80"
                      >
                        <span className="text-muted-foreground/60">○</span>{" "}
                        {formatShortDate(f.observed_at)}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="text-xs text-muted-foreground">
                      {STATUS_LABEL[f.status]}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <Badge variant="outline" className={SEVERITY_CLASS[f.severity]}>
                      {f.severity}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <FindingSlideOver
          finding={selected}
          onClose={() => setSelected(null)}
          onUpdated={handleUpdated}
          onDeleted={(id) => {
            onDeleted(id);
            setSelected(null);
          }}
        />
      )}

      {showAddModal && (
        <AddFindingModal
          slug={slug}
          onClose={() => setShowAddModal(false)}
          onCreated={(f) => {
            // Newest first — prepend into the cache via onUpdated. The
            // parent maps this to setQueryData that already prepends
            // (see upsertFindingInCache) so the row shows up at the top.
            onUpdated(f);
            setShowAddModal(false);
          }}
        />
      )}

      {showCorrelateModal && (
        <CorrelateModal
          slug={slug}
          findings={findings}
          onClose={() => setShowCorrelateModal(false)}
          onMerged={(parent, absorbed) => {
            // The parent row's summary + severity have changed; refresh
            // it in the cache. Every child is now soft-deleted server-
            // side, so drop them from the local view.
            onUpdated(parent);
            absorbed.forEach((cid) => onDeleted(cid));
          }}
        />
      )}

      {showRegroupModal && (
        <RegroupModal
          slug={slug}
          findings={findings}
          onClose={() => setShowRegroupModal(false)}
          onApplied={(absorbedIds) => {
            // Sources are soft-deleted server-side; drop them locally.
            // The parent rows are new (or bumped) — a cache invalidate
            // via a re-list would be cleanest, but for now every drop
            // of a source also removes it from the visible set. The
            // page-level query will refetch the parent on next focus.
            absorbedIds.forEach((id) => onDeleted(id));
          }}
        />
      )}
    </div>
  );
}

type SeverityTone = "critical" | "high" | "info" | "pending";

const SEVERITY_TONE_CLASS: Record<SeverityTone, string> = {
  critical: "border-rose-500/50 bg-rose-500/10 text-rose-100",
  high: "border-pink-400/50 bg-pink-400/10 text-pink-100",
  info: "border-sky-500/50 bg-sky-500/10 text-sky-100",
  pending: "border-orange-500/50 bg-orange-500/10 text-orange-100",
};

const SEVERITY_TONE_VALUE_CLASS: Record<SeverityTone, string> = {
  critical: "text-rose-50",
  high: "text-pink-50",
  info: "text-sky-50",
  pending: "text-orange-50",
};

const SEVERITY_TONE_ACTIVE_RING: Record<SeverityTone, string> = {
  critical: "ring-rose-300/80",
  high: "ring-pink-300/80",
  info: "ring-sky-300/80",
  pending: "ring-orange-300/80",
};

// v0.8.1: tiles are buttons; clicking toggles the corresponding filter.
function SeverityMetricCard({
  label,
  value,
  tone,
  active,
  onClick,
}: {
  label: string;
  value: number;
  tone: SeverityTone;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-lg border p-4 text-left transition-colors",
        SEVERITY_TONE_CLASS[tone],
        active && `ring-2 ${SEVERITY_TONE_ACTIVE_RING[tone]}`,
      )}
    >
      <div
        className={cn(
          "text-2xl font-semibold tabular-nums",
          SEVERITY_TONE_VALUE_CLASS[tone],
        )}
      >
        {value}
      </div>
      <div className="mt-1 text-xs uppercase tracking-wide opacity-80">
        {label}
      </div>
    </button>
  );
}

// Combined Medium + Low card, split diagonally. Each half is its own
// click target — Medium in the top-left wedge, Low in the bottom-right.
// The diagonal is rendered as a CSS linear-gradient with a hard stop at
// 50%; the click hit zones are two absolutely-positioned <button>s
// covering each wedge.
function MediumLowSplitCard({
  medium,
  low,
  mediumActive,
  lowActive,
  onMediumClick,
  onLowClick,
}: {
  medium: number;
  low: number;
  mediumActive: boolean;
  lowActive: boolean;
  onMediumClick: () => void;
  onLowClick: () => void;
}) {
  return (
    <div
      className={cn(
        "relative h-[88px] overflow-hidden rounded-lg border border-yellow-400/40 transition-shadow",
        (mediumActive || lowActive) && "ring-2",
        mediumActive && !lowActive && "ring-yellow-300/80",
        lowActive && !mediumActive && "ring-emerald-300/80",
        mediumActive && lowActive && "ring-foreground/40",
      )}
      style={{
        background:
          "linear-gradient(135deg, rgba(250, 204, 21, 0.18) 0%, rgba(250, 204, 21, 0.18) 49.5%, rgba(255, 255, 255, 0.18) 49.5%, rgba(255, 255, 255, 0.18) 50.5%, rgba(16, 185, 129, 0.18) 50.5%, rgba(16, 185, 129, 0.18) 100%)",
      }}
    >
      {/* Top-left wedge click zone */}
      <button
        type="button"
        onClick={onMediumClick}
        aria-pressed={mediumActive}
        aria-label={`Filter to Medium severity${mediumActive ? " (active)" : ""}`}
        className="absolute inset-0 z-10"
        style={{ clipPath: "polygon(0 0, 100% 0, 0 100%)" }}
      />
      {/* Bottom-right wedge click zone */}
      <button
        type="button"
        onClick={onLowClick}
        aria-pressed={lowActive}
        aria-label={`Filter to Low severity${lowActive ? " (active)" : ""}`}
        className="absolute inset-0 z-10"
        style={{ clipPath: "polygon(100% 0, 100% 100%, 0 100%)" }}
      />
      <div className="pointer-events-none absolute left-3 top-2 leading-tight">
        <div className="text-2xl font-semibold tabular-nums text-yellow-50">
          {medium}
        </div>
        <div className="text-[10px] uppercase tracking-wide text-yellow-100/85">
          Medium
        </div>
      </div>
      <div className="pointer-events-none absolute bottom-2 right-3 text-right leading-tight">
        <div className="text-[10px] uppercase tracking-wide text-emerald-100/85">
          Low
        </div>
        <div className="text-2xl font-semibold tabular-nums text-emerald-50">
          {low}
        </div>
      </div>
    </div>
  );
}

function FilterRow<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: T[];
  value: T;
  onChange: (v: T) => void;
  label: (v: T) => string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          className={cn(
            "rounded-full border px-2.5 py-1 text-xs transition-colors",
            value === opt
              ? "border-critical/50 bg-critical/10 text-foreground"
              : "border-border text-muted-foreground hover:text-foreground",
          )}
        >
          {label(opt)}
        </button>
      ))}
    </div>
  );
}

// ── slide-over: finding detail + validation + attack-path placeholder ──────

// ── Attachment thumbnail (fetches binary with auth, revokes URL on unmount) ──

function AttachmentThumb({
  attachment,
  onDelete,
}: {
  attachment: Attachment;
  onDelete: () => void;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!attachment.content_type.startsWith("image/")) return;
    let objectUrl: string | null = null;
    loadAttachmentBlob(attachment.id)
      .then((url) => { objectUrl = url; setSrc(url); })
      .catch(() => setSrc(null));
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [attachment.id, attachment.content_type]);

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await deleteAttachment(attachment.id);
      onDelete();
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="group relative overflow-hidden rounded border border-border bg-background">
      {src ? (
        <img src={src} alt={attachment.filename} className="h-24 w-full object-cover" />
      ) : (
        <div className="flex h-24 items-center justify-center p-2 text-center font-mono text-[10px] text-muted-foreground">
          {attachment.filename}
        </div>
      )}
      <button
        type="button"
        onClick={handleDelete}
        disabled={deleting}
        className="absolute right-1 top-1 rounded bg-black/60 p-0.5 opacity-0 transition-opacity group-hover:opacity-100"
        aria-label="Delete attachment"
      >
        <X className="h-3 w-3 text-white" />
      </button>
      <p className="truncate px-1.5 py-0.5 text-[10px] text-muted-foreground">
        {attachment.filename}
      </p>
    </div>
  );
}

// ── slide-over ───────────────────────────────────────────────────────────────

function FindingSlideOver({
  finding,
  onClose,
  onUpdated,
  onDeleted,
}: {
  finding: Finding;
  onClose: () => void;
  onUpdated: (f: Finding) => void;
  onDeleted: (findingId: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // v0.10.0: soft-delete confirmation. Two-step so a stray click can't
  // wipe a finding; second click within the same open of the slide-over
  // is the real DELETE.
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [dispatchedIds, setDispatchedIds] = useState<Set<string>>(new Set());
  const [decidingId, setDecidingId] = useState<string | null>(null);

  // Summary editor — the textarea is for the NEXT entry. v0.7.0 made it
  // append-only: every Save records an immutable history row below.
  const [summary, setSummary] = useState("");
  const [savingSummary, setSavingSummary] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  // AI Triage — populates the textarea with an LLM-written summary; the
  // analyst then edits + clicks Save. Does NOT auto-save.
  const [triaging, setTriaging] = useState(false);

  // Summary history (newest first). Refreshed after each Save.
  const [summaries, setSummaries] = useState<FindingSummaryEntry[] | null>(null);
  const [viewingSummary, setViewingSummary] = useState<FindingSummaryEntry | null>(
    null,
  );

  // Attachments
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Load attachments + summary history when the slide-over opens
  useEffect(() => {
    listAttachments(finding.id)
      .then(setAttachments)
      .catch(() => setAttachments([]));
    listFindingSummaries(finding.id)
      .then(setSummaries)
      .catch(() => setSummaries([]));
  }, [finding.id]);

  const doSaveSummary = async () => {
    const trimmed = summary.trim();
    if (!trimmed) {
      setSummaryError("Write a summary first — empty entries aren't recorded.");
      return;
    }
    setSavingSummary(true);
    setSummaryError(null);
    try {
      const entry = await createFindingSummary(finding.id, trimmed);
      // Prepend the new row to the history; clear the textarea so the
      // analyst can start fresh next time.
      setSummaries((prev) => [entry, ...(prev ?? [])]);
      setSummary("");
      // Refresh the parent's cached finding.summary so the Report tab
      // and table can see the new latest. Local-only update; no extra
      // server round-trip.
      onUpdated({ ...finding, summary: entry.body });
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingSummary(false);
    }
  };

  const doTriage = async () => {
    setTriaging(true);
    setSummaryError(null);
    try {
      const res = await triageFinding(finding.id);
      setSummary(res.summary);
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : String(err));
    } finally {
      setTriaging(false);
    }
  };

  const onFileChosen = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      setUploading(true);
      setUploadError(null);
      try {
        const att = await uploadAttachment(finding.id, file);
        setAttachments((prev) => [...prev, att]);
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : String(err));
      } finally {
        setUploading(false);
        e.target.value = "";
      }
    },
    [finding.id],
  );

  const doDelete = async () => {
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteFinding(finding.id);
      onDeleted(finding.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setDeleting(false);
      setConfirmingDelete(false);
    }
  };

  const decide = async (decision: FindingValidationStatus) => {
    setBusy(true);
    setError(null);
    try {
      onUpdated(await validateFinding(finding.id, decision));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // v1.4.0: analyst-set reportability marker. Passing `null` clears the
  // exclusion — the row goes back into the client-facing export.
  const setExclusion = async (next: FindingExclusion | null) => {
    setBusy(true);
    setError(null);
    try {
      onUpdated(await updateFinding(finding.id, { exclusion: next }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // Agents may run scan/enum paths only — never exploitation (CHARTER decided).
  const agentAllowed = finding.phase !== "exploit";

  const runAgent = async () => {
    setAnalyzing(true);
    setAnalyzeError(null);
    try {
      const res = await analyzeFinding(finding.id);
      setSuggestions(res.suggestions);
    } catch (err) {
      setAnalyzeError(err instanceof Error ? err.message : String(err));
    } finally {
      setAnalyzing(false);
    }
  };

  const acceptOne = async (s: Suggestion) => {
    setDecidingId(s.id);
    setAnalyzeError(null);
    try {
      const res = await acceptSuggestion(s.id);
      setSuggestions((prev) =>
        prev?.map((x) => (x.id === s.id ? res.suggestion : x)) ?? null,
      );
      if (res.dispatched) {
        setDispatchedIds((prev) => new Set(prev).add(s.id));
      }
    } catch (err) {
      setAnalyzeError(err instanceof Error ? err.message : String(err));
    } finally {
      setDecidingId(null);
    }
  };

  const dismissOne = async (s: Suggestion) => {
    setDecidingId(s.id);
    setAnalyzeError(null);
    try {
      const updated = await dismissSuggestion(s.id);
      setSuggestions((prev) =>
        prev?.map((x) => (x.id === s.id ? updated : x)) ?? null,
      );
    } catch (err) {
      setAnalyzeError(err instanceof Error ? err.message : String(err));
    } finally {
      setDecidingId(null);
    }
  };

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/60"
        onClick={onClose}
        aria-hidden
      />
      <aside className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col overflow-y-auto border-l border-border bg-popover p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="font-mono text-xs text-muted-foreground">
              {shortId(finding.id)} · {PHASE_LABEL[finding.phase]}
            </div>
            <h2 className="mt-1 text-lg font-semibold leading-tight">
              {finding.title}
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
          <Badge variant="outline" className={SEVERITY_CLASS[finding.severity]}>
            {finding.severity}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {STATUS_LABEL[finding.status]}
          </span>
        </div>

        {finding.target && (
          <p className="mt-3 font-mono text-xs text-muted-foreground">
            target: {finding.target}
          </p>
        )}

        <GroupedItemsPanel finding={finding} />

        <pre className="mt-4 max-h-64 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-xs text-muted-foreground">
          {JSON.stringify(finding.data, null, 2)}
        </pre>

        {/* Summary — analyst narrative that flows into the report. Each
            Save appends an immutable entry to history below; the textarea
            clears so the next observation lands as its own row. */}
        <div className="mt-5">
          <h3 className="text-sm font-medium">Summary</h3>
          <Textarea
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="Write a summary for the report…"
            rows={4}
            className="mt-2 text-sm"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              disabled={savingSummary || !summary.trim()}
              onClick={doSaveSummary}
            >
              {savingSummary ? "Saving…" : "Save summary"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={triaging || savingSummary}
              onClick={doTriage}
              title="Ask the LLM to draft a report-ready summary into the textarea. You can edit, then Save."
            >
              {triaging ? "Triaging…" : "AI Triage"}
            </Button>
            {summaryError && (
              <p className="text-xs text-critical">{summaryError}</p>
            )}
          </div>
        </div>

        {/* Summary history — newest first. Click a card to read the full body. */}
        <div className="mt-5">
          <h3 className="text-sm font-medium">Summary history</h3>
          {summaries === null ? (
            <p className="mt-2 text-xs text-muted-foreground">Loading…</p>
          ) : summaries.length === 0 ? (
            <p className="mt-2 text-xs text-muted-foreground">
              No summaries recorded yet. Save one above to start the history.
            </p>
          ) : (
            <ul className="mt-2 space-y-2">
              {summaries.map((entry) => (
                <li key={entry.id}>
                  <button
                    type="button"
                    onClick={() => setViewingSummary(entry)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-left transition-colors hover:border-foreground/40 hover:bg-secondary/40"
                  >
                    <p className="line-clamp-2 text-xs text-foreground">
                      {entry.body}
                    </p>
                    <p className="mt-1.5 text-[10px] text-muted-foreground">
                      {entry.author_display_name ||
                        entry.author_email ||
                        "(unknown analyst)"}{" "}
                      ·{" "}
                      {new Date(entry.created_at).toLocaleString(undefined, {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Screenshots / evidence attachments */}
        <div className="mt-5">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">Screenshots</h3>
            <Button
              size="sm"
              variant="outline"
              disabled={uploading}
              onClick={() => fileRef.current?.click()}
            >
              <Upload className="mr-1.5 h-3.5 w-3.5" />
              {uploading ? "Uploading…" : "Add"}
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={onFileChosen}
            />
          </div>
          {uploadError && (
            <p className="mt-1 text-xs text-critical">{uploadError}</p>
          )}
          {attachments.length === 0 ? (
            <p className="mt-2 text-xs text-muted-foreground">
              No screenshots attached yet.
            </p>
          ) : (
            <div className="mt-2 grid grid-cols-2 gap-2">
              {attachments.map((att) => (
                <AttachmentThumb
                  key={att.id}
                  attachment={att}
                  onDelete={() =>
                    setAttachments((prev) => prev.filter((a) => a.id !== att.id))
                  }
                />
              ))}
            </div>
          )}
        </div>

        {/* Suggested attack path — Strategic watcher (Phase 9). */}
        <div className="mt-6 rounded-md border border-dashed border-border p-4">
          <h3 className="text-sm font-medium">Suggested attack path</h3>
          <p className="mt-1 text-xs text-muted-foreground/70">
            Strategic proposes next-step tasks (scan / enum only). Accepting
            agent-eligible tasks dispatches a worker run; active tools still
            stop at the approval gate.
          </p>
          <div className="mt-3 flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={!agentAllowed || analyzing}
              onClick={runAgent}
              title={
                agentAllowed
                  ? "Ask Strategic to propose next steps"
                  : "Agents never run exploitation — analyst only"
              }
            >
              {analyzing ? "Thinking…" : "Agent (automate)"}
            </Button>
          </div>
          {!agentAllowed && (
            <p className="mt-2 text-xs text-muted-foreground/60">
              Exploitation is analyst-only — the Agent option is disabled for
              this phase.
            </p>
          )}
          {analyzeError && (
            <p className="mt-2 text-xs text-critical">{analyzeError}</p>
          )}
          {suggestions !== null && suggestions.length === 0 && (
            <p className="mt-3 text-xs text-muted-foreground/70">
              Strategic had no follow-up tasks to propose.
            </p>
          )}
          {suggestions !== null && suggestions.length > 0 && (
            <ul className="mt-3 space-y-2">
              {suggestions.map((s) => (
                <li
                  key={s.id}
                  className="rounded-md border border-border bg-background p-3"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-medium leading-snug">
                        {s.title}
                      </p>
                      {s.body && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          {s.body}
                        </p>
                      )}
                      <p className="mt-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground/60">
                        {String(s.payload.tool ?? "?")} →{" "}
                        {String(s.payload.target ?? "?")}
                        {" · "}
                        {String(s.payload.task_kind ?? "?")}
                      </p>
                    </div>
                    {s.status === "open" && (
                      <div className="flex shrink-0 gap-1">
                        <Button
                          size="sm"
                          disabled={decidingId === s.id}
                          onClick={() => acceptOne(s)}
                        >
                          Accept
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={decidingId === s.id}
                          onClick={() => dismissOne(s)}
                        >
                          Dismiss
                        </Button>
                      </div>
                    )}
                    {s.status !== "open" && (
                      <span className="shrink-0 self-center text-xs text-muted-foreground capitalize">
                        {s.status}
                        {dispatchedIds.has(s.id) ? " · dispatched" : ""}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Validation gate */}
        <div className="mt-auto pt-6">
          {error && <p className="mb-2 text-sm text-critical">{error}</p>}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={busy || finding.status === "validated"}
              onClick={() => decide("validated")}
            >
              Validate
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => decide("rejected")}
            >
              Reject
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => decide("false_positive")}
            >
              False positive
            </Button>
          </div>

          {/* v1.4.0: reportability marker. Orthogonal to validation —
              a validated finding can still be excluded from the client
              deliverable if it's out of scope or off-limits per ROE. */}
          <div className="mt-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">
              Reportability
              {finding.exclusion && (
                <span className="ml-2 rounded border border-border/70 bg-secondary/50 px-1.5 py-0.5 text-[10px] tracking-normal text-foreground">
                  {EXCLUSION_LABEL[finding.exclusion]}
                </span>
              )}
            </p>
            <div className="mt-1.5 flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={busy || finding.exclusion === "out_of_scope"}
                onClick={() => setExclusion("out_of_scope")}
                title="Real finding, but not in the client-declared scope. Report exporter drops it when 'Omit excluded' is on."
              >
                Out of scope
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy || finding.exclusion === "outside_roe"}
                onClick={() => setExclusion("outside_roe")}
                title="Real finding, but off-limits per the engagement's rules of engagement / legal terms."
              >
                Outside ROE
              </Button>
              {finding.exclusion && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => setExclusion(null)}
                >
                  Clear
                </Button>
              )}
            </div>
          </div>

          {/* v0.10.0: soft-delete. Two-step confirm; audit_log records
              the actor + timestamp so a future recovery view can un-hide. */}
          <div className="mt-3 flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={deleting}
              onClick={doDelete}
              className={
                confirmingDelete
                  ? "border-rose-500 bg-rose-500/10 text-rose-100 hover:bg-rose-500/20"
                  : "border-rose-500/40 text-rose-300 hover:bg-rose-500/10"
              }
              title="Soft-delete this finding — hidden from Findings, Report, and JSON export. audit_log preserves who + when."
            >
              <Trash2 className="mr-1.5 h-3.5 w-3.5" />
              {deleting
                ? "Deleting…"
                : confirmingDelete
                  ? "Click again to confirm"
                  : "Delete finding"}
            </Button>
            {confirmingDelete && !deleting && (
              <button
                type="button"
                onClick={() => setConfirmingDelete(false)}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                Cancel
              </button>
            )}
          </div>
        </div>
      </aside>

      {/* Summary detail popup — opens when the analyst clicks a row in
          Summary history. z-60 so it sits over the slide-over. */}
      {viewingSummary && (
        <>
          <div
            className="fixed inset-0 z-[60] bg-black/70"
            onClick={() => setViewingSummary(null)}
            aria-hidden
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Summary detail"
            className="fixed left-1/2 top-1/2 z-[70] flex max-h-[80vh] w-[min(640px,92vw)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-border bg-popover p-5 shadow-xl"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h3 className="text-sm font-semibold text-foreground">
                  Summary recorded{" "}
                  {new Date(viewingSummary.created_at).toLocaleString()}
                </h3>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  by{" "}
                  {viewingSummary.author_display_name ||
                    viewingSummary.author_email ||
                    "(unknown analyst)"}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setViewingSummary(null)}
                className="text-muted-foreground hover:text-foreground"
                aria-label="Close"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="mt-4 overflow-y-auto whitespace-pre-wrap text-sm text-foreground">
              {viewingSummary.body}
            </div>
          </div>
        </>
      )}
    </>
  );
}

// ── v1.4.0 (part 2): Grouped items panel ───────────────────────────────────

// Rendered inside the slide-over when a finding has data.items[] — one
// row per hit (subdomain, open port, affected URL, etc.). Falls back to
// null when the finding is un-grouped so the old JSON dump is the only
// detail view for legacy rows.
function GroupedItemsPanel({ finding }: { finding: Finding }) {
  const items = Array.isArray((finding.data as { items?: unknown }).items)
    ? ((finding.data as { items: unknown[] }).items as Record<string, unknown>[])
    : null;
  if (!items || items.length === 0) return null;

  // Column keys = union of every item's keys, minus internal-only ones.
  // Preserves order-of-first-appearance for stable column ordering.
  const columns: string[] = [];
  const seen = new Set<string>();
  for (const it of items) {
    if (!it || typeof it !== "object") continue;
    for (const k of Object.keys(it)) {
      if (seen.has(k)) continue;
      if (k === "first_seen_at") continue;
      seen.add(k);
      columns.push(k);
    }
  }

  return (
    <div className="mt-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">
          <Layers className="mr-1.5 inline h-3.5 w-3.5 -translate-y-0.5" />
          Items ({items.length})
        </h3>
        {finding.group_key && (
          <span
            className="font-mono text-[10px] text-muted-foreground"
            title="Grouping key"
          >
            {finding.group_key}
          </span>
        )}
      </div>
      <p className="mt-0.5 text-[11px] text-muted-foreground/80">
        Every re-run of this tool against the same target folds into this
        row. Individual hits are deduped by their natural key.
      </p>
      <div className="mt-2 max-h-64 overflow-auto rounded-md border border-border">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 bg-popover">
            <tr className="border-b border-border text-left">
              {columns.map((c) => (
                <th
                  key={c}
                  className="px-2 py-1.5 font-medium text-muted-foreground"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((it, idx) => (
              <tr
                key={idx}
                className="border-b border-border/40 last:border-0"
              >
                {columns.map((c) => {
                  const v = it[c];
                  return (
                    <td
                      key={c}
                      className="px-2 py-1.5 font-mono text-[11px] text-foreground/90"
                    >
                      {v === null || v === undefined
                        ? "—"
                        : typeof v === "object"
                          ? JSON.stringify(v)
                          : String(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── v1.4.1: Regroup modal ──────────────────────────────────────────────────

// Renders the preview response from POST /findings/regroup/preview and
// lets the analyst toggle per-group Apply. Same visual pattern as the
// Correlate modal — but deterministic and instant (no LLM call).

type RegroupRow = RegroupProposal & {
  status: "open" | "applying" | "applied" | "skipped";
  error?: string;
};

function RegroupModal({
  slug,
  findings,
  onClose,
  onApplied,
}: {
  slug: string;
  findings: Finding[];
  onClose: () => void;
  onApplied: (absorbedFindingIds: string[]) => void;
}) {
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [scannedCount, setScannedCount] = useState(0);
  const [ungroupableCount, setUngroupableCount] = useState(0);
  const [rows, setRows] = useState<RegroupRow[]>([]);
  const [applying, setApplying] = useState(false);

  const byId = useMemo(() => {
    const map = new Map<string, Finding>();
    for (const f of findings) map.set(f.id, f);
    return map;
  }, [findings]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setPhase("loading");
      setErrorText(null);
      try {
        const res = await regroupFindingsPreview(slug);
        if (cancelled) return;
        setScannedCount(res.scanned_row_count);
        setUngroupableCount(res.ungroupable_count);
        setRows(res.proposals.map((p) => ({ ...p, status: "open" })));
        setPhase("ready");
      } catch (err) {
        if (cancelled) return;
        setErrorText(err instanceof Error ? err.message : String(err));
        setPhase("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const toggleSkip = (idx: number) => {
    setRows((prev) => {
      const next = prev.slice();
      const g = next[idx];
      if (!g || g.status === "applying" || g.status === "applied") return prev;
      next[idx] = { ...g, status: g.status === "skipped" ? "open" : "skipped" };
      return next;
    });
  };

  const apply = async () => {
    const approved = rows.filter((r) => r.status === "open");
    if (approved.length === 0) return;
    setApplying(true);
    // Flip each approved row's status to applying for feedback.
    setRows((prev) =>
      prev.map((r) => (r.status === "open" ? { ...r, status: "applying" } : r)),
    );
    try {
      const results = await regroupFindingsApply(
        slug,
        approved.map((r) => r.group_key),
      );
      const appliedKeys = new Set(results.map((r) => r.group_key));
      const absorbedIds: string[] = [];
      for (const r of approved) {
        if (appliedKeys.has(r.group_key)) {
          for (const mid of r.member_ids) absorbedIds.push(mid);
        }
      }
      onApplied(absorbedIds);
      setRows((prev) =>
        prev.map((r) =>
          appliedKeys.has(r.group_key) && r.status === "applying"
            ? { ...r, status: "applied" }
            : r,
        ),
      );
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : String(err));
      // Roll applying back to open so the analyst can retry.
      setRows((prev) =>
        prev.map((r) => (r.status === "applying" ? { ...r, status: "open" } : r)),
      );
    } finally {
      setApplying(false);
    }
  };

  const openCount = rows.filter((r) => r.status === "open").length;

  return (
    <>
      <div
        className="fixed inset-0 z-[60] bg-black/70"
        onClick={onClose}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Group findings"
        className="fixed left-1/2 top-1/2 z-[70] flex max-h-[90vh] w-[min(760px,94vw)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-border bg-popover shadow-xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h3 className="text-sm font-semibold text-foreground">
              <Wand2 className="mr-1.5 inline h-4 w-4 -translate-y-0.5" />
              Group findings
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Deterministic auto-group: every ungrouped row runs through
              the v1.4.0 tool vocab. Rows that would share a key fold
              into one. Nothing happens until you Apply.
            </p>
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

        <div className="overflow-y-auto px-5 py-4">
          {phase === "loading" && (
            <p className="text-sm text-muted-foreground">Analyzing…</p>
          )}

          {phase === "error" && (
            <div className="rounded-md border border-critical/40 bg-critical/10 p-3 text-sm text-critical">
              {errorText}
            </div>
          )}

          {phase === "ready" && rows.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {scannedCount === 0
                ? "No ungrouped rows to work with — every finding is already grouped."
                : ungroupableCount === scannedCount
                  ? `Scanned ${scannedCount} ungrouped rows; the tool vocab couldn't key any of them (custom tools or manual entries).`
                  : `Scanned ${scannedCount} ungrouped rows and found no clusters — every row's category is unique.`}
            </p>
          )}

          {phase === "ready" && rows.length > 0 && (
            <>
              <p className="mb-3 text-xs text-muted-foreground">
                Scanned {scannedCount} ungrouped rows · {ungroupableCount}{" "}
                without a matchable tool · {rows.length} group
                {rows.length === 1 ? "" : "s"} proposed
              </p>
              <ul className="space-y-2">
                {rows.map((g, idx) => {
                  const members = g.member_ids
                    .map((id) => byId.get(id))
                    .filter((f): f is Finding => Boolean(f));
                  const isDone = g.status === "applied";
                  const isSkipped = g.status === "skipped";
                  const isBusy = g.status === "applying";
                  return (
                    <li
                      key={g.group_key}
                      className={cn(
                        "rounded-md border border-border bg-background p-3",
                        isSkipped && "opacity-40",
                        isDone && "border-emerald-500/40 bg-emerald-500/5",
                      )}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="flex flex-wrap items-center gap-2 text-sm text-foreground">
                            <span className="font-medium">
                              {g.proposed_title}
                            </span>
                            <Badge
                              variant="outline"
                              className={cn(
                                "text-[10px]",
                                SEVERITY_CLASS[g.projected_severity],
                              )}
                            >
                              {g.projected_severity}
                            </Badge>
                            <span
                              className="rounded border border-sky-500/40 bg-sky-500/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-sky-200"
                              title={`Group key: ${g.group_key}`}
                            >
                              × {g.projected_item_count} items
                            </span>
                            {g.absorbs_into_existing_parent_id && (
                              <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-amber-100">
                                absorbs existing
                              </span>
                            )}
                          </p>
                          <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                            {g.group_key}
                          </p>
                          <p className="mt-1.5 text-xs text-muted-foreground">
                            {members.length} row
                            {members.length === 1 ? "" : "s"} to absorb:{" "}
                            {members
                              .slice(0, 4)
                              .map((f) => shortId(f.id))
                              .join(", ")}
                            {members.length > 4 && ` +${members.length - 4} more`}
                          </p>
                        </div>
                        <div className="flex shrink-0 flex-col items-end gap-1">
                          {isDone ? (
                            <span className="text-xs text-emerald-300">
                              <Layers className="mr-1 inline h-3.5 w-3.5" />
                              merged
                            </span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => toggleSkip(idx)}
                              disabled={isBusy || applying}
                              className={cn(
                                "text-xs underline-offset-2 hover:underline",
                                isSkipped
                                  ? "text-muted-foreground"
                                  : "text-critical",
                              )}
                            >
                              {isSkipped ? "un-skip" : "skip"}
                            </button>
                          )}
                        </div>
                      </div>
                      {g.error && (
                        <p className="mt-2 text-xs text-critical">{g.error}</p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-border px-5 py-3">
          <p className="text-xs text-muted-foreground">
            {rows.length > 0 && (
              <>
                {openCount} group{openCount === 1 ? "" : "s"} pending ·{" "}
                {rows.filter((r) => r.status === "applied").length} applied
              </>
            )}
          </p>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>
              Close
            </Button>
            <Button
              size="sm"
              disabled={applying || openCount === 0}
              onClick={apply}
            >
              {applying
                ? "Applying…"
                : `Apply ${openCount} group${openCount === 1 ? "" : "s"}`}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

// ── v1.4.0: Add Finding modal ──────────────────────────────────────────────

function AddFindingModal({
  slug,
  onClose,
  onCreated,
}: {
  slug: string;
  onClose: () => void;
  onCreated: (finding: Finding) => void;
}) {
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [target, setTarget] = useState("");
  const [severity, setSeverity] = useState<Severity>("info");
  const [phase, setPhase] = useState<FindingPhase>("general");
  // <input type="date"> value format is YYYY-MM-DD. We turn empty into
  // null before shipping; a set value becomes an ISO string at UTC noon
  // so the calendar day round-trips regardless of the analyst's TZ.
  const [observedAt, setObservedAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = title.trim().length > 0 && !busy;

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const observedIso = observedAt
        ? new Date(`${observedAt}T12:00:00Z`).toISOString()
        : null;
      const finding = await createFinding(slug, {
        title: title.trim(),
        summary: summary.trim() || null,
        severity,
        phase,
        target: target.trim() || null,
        observed_at: observedIso,
      });
      onCreated(finding);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <>
      <div
        className="fixed inset-0 z-[60] bg-black/70"
        onClick={onClose}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Add finding"
        className="fixed left-1/2 top-1/2 z-[70] flex max-h-[90vh] w-[min(560px,94vw)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-border bg-popover shadow-xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h3 className="text-sm font-semibold text-foreground">Add finding</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Hand-type a finding the tooling didn&apos;t surface. New row
              lands at the top of the Findings tab.
            </p>
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

        <div className="grid gap-4 overflow-y-auto px-5 py-4">
          <div>
            <Label htmlFor="add-finding-title">Title *</Label>
            <Input
              id="add-finding-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Reflected XSS in /search endpoint"
              className="mt-1.5"
              autoFocus
            />
          </div>

          <div>
            <Label htmlFor="add-finding-summary">Details</Label>
            <Textarea
              id="add-finding-summary"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              rows={4}
              placeholder="What did you observe? Impact, evidence, reproduction steps."
              className="mt-1.5 text-sm"
            />
          </div>

          <div>
            <Label htmlFor="add-finding-target">Target</Label>
            <Input
              id="add-finding-target"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="host / URL / entity affected"
              className="mt-1.5 font-mono text-xs"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label htmlFor="add-finding-severity">Severity</Label>
              <select
                id="add-finding-severity"
                value={severity}
                onChange={(e) => setSeverity(e.target.value as Severity)}
                className="mt-1.5 h-9 w-full rounded-md border border-border bg-background px-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {SEVERITY_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <Label htmlFor="add-finding-phase">Phase</Label>
              <select
                id="add-finding-phase"
                value={phase}
                onChange={(e) => setPhase(e.target.value as FindingPhase)}
                className="mt-1.5 h-9 w-full rounded-md border border-border bg-background px-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {PHASE_OPTIONS.map((p) => (
                  <option key={p} value={p}>
                    {PHASE_LABEL[p]}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <Label htmlFor="add-finding-observed">Observed on</Label>
            <Input
              id="add-finding-observed"
              type="date"
              value={observedAt}
              onChange={(e) => setObservedAt(e.target.value)}
              className="mt-1.5"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              When the issue was seen in the wild. Leave empty to fall back
              to today.
            </p>
          </div>

          {error && (
            <p className="text-xs text-critical" role="alert">
              {error}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
          <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button size="sm" onClick={submit} disabled={!canSubmit}>
            {busy ? "Creating…" : "Create finding"}
          </Button>
        </div>
      </div>
    </>
  );
}

// ── v1.4.0: Correlate modal ────────────────────────────────────────────────

// Local shape — the modal keeps a mutable copy of the response groups so
// it can drop the ones the analyst has approved / dismissed without a
// round-trip.
type ModalGroup = CorrelateGroup & {
  status: "open" | "merging" | "merged" | "dismissed";
  parentId: string;
  error?: string;
};

function CorrelateModal({
  slug,
  findings,
  onClose,
  onMerged,
}: {
  slug: string;
  findings: Finding[];
  onClose: () => void;
  onMerged: (parent: Finding, absorbed: string[]) => void;
}) {
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [totalConsidered, setTotalConsidered] = useState(0);
  const [groups, setGroups] = useState<ModalGroup[]>([]);

  const byId = useMemo(() => {
    const map = new Map<string, Finding>();
    for (const f of findings) map.set(f.id, f);
    return map;
  }, [findings]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setPhase("loading");
      setErrorText(null);
      try {
        const res = await correlateFindings(slug);
        if (cancelled) return;
        setTotalConsidered(res.total_considered);
        setGroups(
          res.groups.map((g) => ({
            ...g,
            status: "open",
            parentId: g.finding_ids[0] ?? "",
          })),
        );
        setPhase("ready");
      } catch (err) {
        if (cancelled) return;
        setErrorText(err instanceof Error ? err.message : String(err));
        setPhase("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const approve = async (idx: number) => {
    const g = groups[idx];
    if (!g || g.status !== "open") return;
    const parent = byId.get(g.parentId);
    if (!parent) {
      setGroups((prev) => {
        const next = prev.slice();
        next[idx] = { ...g, error: "parent finding no longer exists" };
        return next;
      });
      return;
    }
    const childIds = g.finding_ids.filter((id) => id !== g.parentId);
    setGroups((prev) => {
      const next = prev.slice();
      next[idx] = { ...g, status: "merging", error: undefined };
      return next;
    });
    try {
      const merged = await mergeFindings(g.parentId, childIds);
      onMerged(merged, childIds);
      setGroups((prev) => {
        const next = prev.slice();
        next[idx] = { ...g, status: "merged" };
        return next;
      });
    } catch (err) {
      setGroups((prev) => {
        const next = prev.slice();
        next[idx] = {
          ...g,
          status: "open",
          error: err instanceof Error ? err.message : String(err),
        };
        return next;
      });
    }
  };

  const dismiss = (idx: number) => {
    setGroups((prev) => {
      const next = prev.slice();
      const g = next[idx];
      if (g) next[idx] = { ...g, status: "dismissed" };
      return next;
    });
  };

  const setParent = (idx: number, parentId: string) => {
    setGroups((prev) => {
      const next = prev.slice();
      const g = next[idx];
      if (g && g.status === "open") next[idx] = { ...g, parentId };
      return next;
    });
  };

  const openGroups = groups.filter((g) => g.status !== "dismissed");
  const remainingOpen = openGroups.filter((g) => g.status === "open").length;

  return (
    <>
      <div
        className="fixed inset-0 z-[60] bg-black/70"
        onClick={onClose}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Correlate findings"
        className="fixed left-1/2 top-1/2 z-[70] flex max-h-[90vh] w-[min(760px,94vw)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-border bg-popover shadow-xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h3 className="text-sm font-semibold text-foreground">
              <Sparkles className="mr-1.5 inline h-4 w-4 -translate-y-0.5" />
              Correlate findings
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              The agent groups findings that likely describe the same root
              cause. Approve to merge — the first row in each group becomes
              the parent; the others fold in.
            </p>
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

        <div className="overflow-y-auto px-5 py-4">
          {phase === "loading" && (
            <p className="text-sm text-muted-foreground">
              Thinking — asking the agent to look for related findings…
            </p>
          )}

          {phase === "error" && (
            <div className="rounded-md border border-critical/40 bg-critical/10 p-3 text-sm text-critical">
              {errorText}
            </div>
          )}

          {phase === "ready" && groups.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {totalConsidered === 0
                ? "No open findings to correlate — every row is already resolved or excluded."
                : totalConsidered === 1
                  ? "Only one open finding — nothing to group against."
                  : `The agent considered ${totalConsidered} open findings and didn't find any that clearly group together. That's a normal result when everything is genuinely distinct.`}
            </p>
          )}

          {phase === "ready" && groups.length > 0 && (
            <>
              <p className="mb-3 text-xs text-muted-foreground">
                Considered {totalConsidered} open findings ·{" "}
                {remainingOpen} group{remainingOpen === 1 ? "" : "s"} awaiting
                a decision
              </p>
              <ul className="space-y-3">
                {groups.map((g, idx) => {
                  if (g.status === "dismissed") return null;
                  const members = g.finding_ids
                    .map((id) => byId.get(id))
                    .filter((f): f is Finding => Boolean(f));
                  return (
                    <li
                      key={idx}
                      className={cn(
                        "rounded-md border border-border bg-background p-3",
                        g.status === "merged" && "opacity-60",
                      )}
                    >
                      <p className="text-sm text-foreground">{g.rationale}</p>
                      <ul className="mt-2 space-y-1.5">
                        {members.map((f) => (
                          <li
                            key={f.id}
                            className="flex items-start gap-2 text-xs"
                          >
                            <input
                              type="radio"
                              name={`parent-${idx}`}
                              value={f.id}
                              checked={g.parentId === f.id}
                              disabled={g.status !== "open"}
                              onChange={() => setParent(idx, f.id)}
                              className="mt-0.5 cursor-pointer accent-critical"
                              aria-label={`Choose ${shortId(f.id)} as parent`}
                            />
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <span className="font-mono text-[10px] text-muted-foreground">
                                  {shortId(f.id)}
                                </span>
                                <Badge
                                  variant="outline"
                                  className={cn(
                                    "text-[10px]",
                                    SEVERITY_CLASS[f.severity],
                                  )}
                                >
                                  {f.severity}
                                </Badge>
                                <span className="truncate text-foreground">
                                  {f.title}
                                </span>
                              </div>
                              {f.target && (
                                <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                                  {f.target}
                                </p>
                              )}
                            </div>
                          </li>
                        ))}
                      </ul>

                      {g.error && (
                        <p className="mt-2 text-xs text-critical">{g.error}</p>
                      )}

                      <div className="mt-3 flex justify-end gap-2">
                        {g.status === "merged" ? (
                          <span className="text-xs text-muted-foreground">
                            <Layers className="mr-1 inline h-3.5 w-3.5" />
                            Merged into {shortId(g.parentId)}
                          </span>
                        ) : (
                          <>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={g.status === "merging"}
                              onClick={() => dismiss(idx)}
                            >
                              Reject
                            </Button>
                            <Button
                              size="sm"
                              disabled={g.status === "merging"}
                              onClick={() => approve(idx)}
                            >
                              {g.status === "merging"
                                ? "Merging…"
                                : `Approve · merge ${g.finding_ids.length - 1} into parent`}
                            </Button>
                          </>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
          <Button variant="outline" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </>
  );
}
