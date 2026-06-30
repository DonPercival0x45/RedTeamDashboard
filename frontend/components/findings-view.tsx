"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Upload, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  acceptSuggestion,
  analyzeFinding,
  createFindingSummary,
  deleteAttachment,
  dismissSuggestion,
  listAttachments,
  listFindingSummaries,
  loadAttachmentBlob,
  triageFinding,
  uploadAttachment,
  validateFinding,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { FindingImporter } from "@/components/finding-importer";
import { BurpImporter } from "@/components/burp-importer";
import type {
  Attachment,
  Finding,
  FindingPhase,
  FindingSort,
  FindingSummaryEntry,
  FindingValidationStatus,
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
}: {
  slug: string;
  findings: Finding[];
  onUpdated: (finding: Finding) => void;
}) {
  const [phase, setPhase] = useState<FindingPhase | "all">("all");
  const [status, setStatus] = useState<FindingValidationStatus | "all">("all");
  const [sort, setSort] = useState<FindingSort>("newest");
  const [selected, setSelected] = useState<Finding | null>(null);
  const [showImporter, setShowImporter] = useState(false);
  const [showBurpImporter, setShowBurpImporter] = useState(false);

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

  const visible = findings
    .filter((f) => phase === "all" || f.phase === phase)
    .filter((f) => status === "all" || f.status === status)
    .filter((f) => severityFilter === "all" || f.severity === severityFilter)
    .slice()
    .sort(compareFindings);

  const handleUpdated = (f: Finding) => {
    onUpdated(f);
    setSelected(f);
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
        <div className="ml-auto flex gap-2">
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
                  className="cursor-pointer border-b border-border/60 align-top last:border-0 hover:bg-secondary/40"
                >
                  <td className="px-3 py-2.5 font-mono text-xs text-muted-foreground">
                    {shortId(f.id)}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{f.title}</span>
                      {f.tool === "import" && (
                        <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          imported
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {PHASE_LABEL[f.phase]}
                      {f.tool && f.tool !== "import" ? ` · ${f.tool}` : ""}
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
}: {
  finding: Finding;
  onClose: () => void;
  onUpdated: (f: Finding) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
