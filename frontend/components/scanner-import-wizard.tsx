"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Search, UploadCloud } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { commitScannerImport, previewScannerImport } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  Finding,
  ScannerImportCommitResult,
  ScannerImportPreview,
  ScannerImportSource,
  ScannerPreviewGroup,
  Severity,
} from "@/lib/types";

type Step = "upload" | "preview" | "confirm" | "result";
type ScopeFilter = "all" | "in_scope" | "mixed" | "out_of_scope";

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];
const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "border-critical/50 bg-critical/10 text-critical",
  high: "border-orange-500/40 text-orange-700 dark:text-orange-300",
  medium: "border-amber-500/40 text-amber-700 dark:text-amber-300",
  low: "border-emerald-500/40 text-emerald-700 dark:text-emerald-300",
  info: "border-sky-500/40 text-sky-700 dark:text-sky-300",
};

function sourceLabel(source: ScannerImportSource): string {
  return source === "nmap" ? "Nmap" : source[0].toUpperCase() + source.slice(1);
}

function scopeState(group: ScannerPreviewGroup): Exclude<ScopeFilter, "all"> {
  if (group.in_scope_item_count === 0) return "out_of_scope";
  if (group.out_of_scope_item_count > 0) return "mixed";
  return "in_scope";
}

function disabledGroup(group: ScannerPreviewGroup): boolean {
  return group.in_scope_item_count === 0 || group.duplicate_state === "existing";
}

export function ScannerImportWizard({
  slug,
  source,
  onImported,
  initialFile = null,
}: {
  slug: string;
  source: ScannerImportSource;
  onImported: (findings: Finding[]) => void;
  initialFile?: File | null;
}) {
  const [step, setStep] = useState<Step>("upload");
  const [file, setFile] = useState<File | null>(initialFile);
  const [preview, setPreview] = useState<ScannerImportPreview | null>(null);
  const [result, setResult] = useState<ScannerImportCommitResult | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState<"all" | Severity>("all");
  const [scope, setScope] = useState<ScopeFilter>("all");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!initialFile) return;
    setFile(initialFile);
    setPreview(null);
    setResult(null);
    setStep("upload");
  }, [initialFile]);

  const visibleGroups = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (preview?.groups ?? []).filter((group) => {
      if (severity !== "all" && group.severity !== severity) return false;
      if (scope !== "all" && scopeState(group) !== scope) return false;
      if (!query) return true;
      return [group.title, group.selection_key, ...group.targets]
        .join(" ")
        .toLowerCase()
        .includes(query);
    });
  }, [preview, scope, search, severity]);

  const selectableVisible = visibleGroups.filter((group) => !disabledGroup(group));
  const selectedGroups = preview?.groups.filter((group) => selected.has(group.selection_key)) ?? [];
  const projectedItems = selectedGroups.reduce(
    (total, group) => total + group.in_scope_item_count - group.duplicate_item_count,
    0,
  );

  async function runPreview() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      // Ask the backend to include informational rows in the preview so the
      // wizard can show them clearly while leaving them unselected by default.
      const next = await previewScannerImport(slug, source, file, true);
      setPreview(next);
      setSelected(
        new Set(
          next.groups
            .filter((group) => group.default_selected && !disabledGroup(group))
            .map((group) => group.selection_key),
        ),
      );
      setStep("preview");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function runCommit() {
    if (!file || !preview || selected.size === 0) return;
    setBusy(true);
    setError(null);
    try {
      const next = await commitScannerImport(
        slug,
        source,
        file,
        preview.file_sha256,
        Array.from(selected).sort(),
      );
      setResult(next);
      onImported(next.imported);
      setStep("result");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function reset(nextFile: File | null = null) {
    setFile(nextFile);
    setPreview(null);
    setResult(null);
    setSelected(new Set());
    setSearch("");
    setSeverity("all");
    setScope("all");
    setError(null);
    setStep("upload");
  }

  function toggle(key: string, checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  function selectVisible(checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      for (const group of selectableVisible) {
        if (checked) next.add(group.selection_key);
        else next.delete(group.selection_key);
      }
      return next;
    });
  }

  return (
    <div className="space-y-4 rounded-md border border-border bg-background p-3">
      <ol aria-label="Import progress" className="flex flex-wrap gap-2 text-xs">
        {(["upload", "preview", "confirm", "result"] as Step[]).map((name, index) => (
          <li
            key={name}
            className={cn(
              "rounded-full border px-2 py-1 capitalize",
              step === name ? "border-foreground/40 bg-secondary text-foreground" : "text-muted-foreground",
            )}
          >
            {index + 1}. {name}
          </li>
        ))}
      </ol>

      {error && <p role="alert" className="rounded border border-critical/40 bg-critical/10 p-2 text-sm text-critical">{error}</p>}

      {step === "upload" && (
        <section className="space-y-3" aria-labelledby={`${source}-upload-title`}>
          <div>
            <h3 id={`${source}-upload-title`} className="text-sm font-medium">Upload {sourceLabel(source)} export</h3>
            <p className="text-xs text-muted-foreground">Preview is read-only. The same browser file is reparsed after confirmation.</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={fileRef}
              type="file"
              accept=".xml,.nessus,application/xml,text/xml"
              onChange={(event) => reset(event.target.files?.[0] ?? null)}
              className="text-xs file:mr-2 file:rounded file:border file:border-border file:bg-secondary file:px-2 file:py-1"
            />
            {file && <span className="text-xs text-muted-foreground">{file.name} · {(file.size / 1024).toFixed(1)} KB</span>}
            <Button type="button" size="sm" disabled={!file || busy} onClick={runPreview}>
              <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
              {busy ? "Previewing…" : "Preview file"}
            </Button>
          </div>
        </section>
      )}

      {step === "preview" && preview && (
        <section className="space-y-3" aria-labelledby={`${source}-preview-title`}>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h3 id={`${source}-preview-title`} className="text-sm font-medium">Review proposed groups</h3>
              <p className="text-xs text-muted-foreground">
                {preview.groups.length} groups · {preview.total_source_rows} source rows · informational groups default off
              </p>
            </div>
            <Button type="button" size="sm" variant="outline" onClick={() => reset()}>Change file</Button>
          </div>

          <div className="grid gap-2 md:grid-cols-[1fr_auto_auto]">
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search title or target" className="pl-8" />
            </div>
            <select aria-label="Filter severity" value={severity} onChange={(event) => setSeverity(event.target.value as "all" | Severity)} className="rounded border border-border bg-background px-2 text-sm">
              <option value="all">All severities</option>
              {SEVERITIES.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
            <select aria-label="Filter scope status" value={scope} onChange={(event) => setScope(event.target.value as ScopeFilter)} className="rounded border border-border bg-background px-2 text-sm">
              <option value="all">All scope states</option>
              <option value="in_scope">In scope</option>
              <option value="mixed">Mixed scope</option>
              <option value="out_of_scope">Out of scope</option>
            </select>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => selectVisible(true)}>Select visible</Button>
            <Button type="button" size="sm" variant="outline" onClick={() => selectVisible(false)}>Deselect visible</Button>
            <span className="text-xs text-muted-foreground">{selected.size} groups selected</span>
          </div>

          <ul className="max-h-[28rem] space-y-2 overflow-auto" aria-label="Scanner preview groups">
            {visibleGroups.map((group) => {
              const disabled = disabledGroup(group);
              const state = scopeState(group);
              return (
                <li key={group.selection_key} className={cn("rounded border border-border p-3", disabled && "bg-secondary/30 opacity-75")}>
                  <label className="flex items-start gap-3">
                    <input type="checkbox" className="mt-1" checked={selected.has(group.selection_key)} disabled={disabled} onChange={(event) => toggle(group.selection_key, event.target.checked)} />
                    <span className="min-w-0 flex-1 space-y-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <strong className="text-sm font-medium">{group.title}</strong>
                        <Badge variant="outline" className={SEVERITY_CLASS[group.severity]}>{group.severity}</Badge>
                        {group.severity === "info" && <Badge variant="outline">default off</Badge>}
                        <Badge variant="outline">{state.replaceAll("_", " ")}</Badge>
                        {group.duplicate_state !== "new" && <Badge variant="outline">{group.duplicate_state} duplicate</Badge>}
                      </span>
                      <span className="block text-xs text-muted-foreground">
                        {group.item_count} items · {group.in_scope_item_count} in scope · {group.duplicate_item_count} duplicate
                      </span>
                      {group.targets.length > 0 && <span className="block truncate text-xs text-muted-foreground">Targets: {group.targets.join(", ")}{group.targets_truncated ? " …" : ""}</span>}
                      <span className="block text-xs text-muted-foreground">{group.scope_reasons.map((reason) => `${reason.count} ${reason.message}`).join(" · ")}</span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>

          <div className="flex justify-end">
            <Button type="button" size="sm" disabled={selected.size === 0} onClick={() => setStep("confirm")}>Review {selected.size} selected</Button>
          </div>
        </section>
      )}

      {step === "confirm" && preview && (
        <section className="space-y-3" aria-labelledby={`${source}-confirm-title`}>
          <h3 id={`${source}-confirm-title`} className="text-sm font-medium">Confirm scanner import</h3>
          <div className="rounded border border-border bg-secondary/30 p-3 text-sm">
            <p><strong>{selected.size}</strong> groups selected from <strong>{file?.name}</strong>.</p>
            <p className="text-xs text-muted-foreground">Up to {Math.max(projectedItems, 0)} new in-scope items will be applied. The server will verify the file hash, reparse it, and recheck scope and duplicates.</p>
          </div>
          <div className="flex justify-between">
            <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setStep("preview")}>Back</Button>
            <Button type="button" size="sm" disabled={busy || selected.size === 0} onClick={runCommit}>{busy ? "Importing…" : "Confirm import"}</Button>
          </div>
        </section>
      )}

      {step === "result" && result && (
        <section className="space-y-3" aria-labelledby={`${source}-result-title`}>
          <div className="flex items-start gap-2 rounded border border-emerald-500/30 bg-emerald-500/10 p-3">
            <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-600" />
            <div>
              <h3 id={`${source}-result-title`} className="text-sm font-medium">Import complete</h3>
              <p className="text-xs text-muted-foreground">{result.imported.length} finding rows affected · {result.selected_item_count} new items · {result.skipped_duplicate} duplicates skipped · {result.skipped_out_of_scope} out of scope</p>
            </div>
          </div>
          {result.imported.length > 0 && (
            <ul className="space-y-1 rounded border border-border p-2 text-xs">
              {result.imported.slice(0, 10).map((finding) => (
                <li key={finding.id}>{finding.title}</li>
              ))}
              {result.imported.length > 10 && <li className="text-muted-foreground">+ {result.imported.length - 10} more</li>}
            </ul>
          )}
          <div className="flex justify-between gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => reset()}>Import another</Button>
            <Button asChild size="sm"><a href="?view=findings">Review all findings</a></Button>
          </div>
        </section>
      )}
    </div>
  );
}
