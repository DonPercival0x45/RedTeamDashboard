"use client";

// Burp Pro Issue Export XML importer panel (v0.7.0).
//
// Sits alongside FindingImporter on the Findings tab. Accepts the XML you
// get from Burp's "Export issues → XML" dialog (Issue Activity or Site
// Map → right-click selected issues → Report selected issues → XML).
// Each <issue> becomes a Finding row at phase=vuln_scan with
// source_tool=burp_import, dedup'd by <serialNumber> against the
// engagement's existing findings.

import { useCallback, useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { importFindingsFromBurp } from "@/lib/api";
import type { BurpImportResult, Finding } from "@/lib/types";

export function BurpImporter({
  slug,
  onImported,
}: {
  slug: string;
  onImported: (created: Finding[]) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [includeInfo, setIncludeInfo] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BurpImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const onPick = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setError(null);
      setResult(null);
      const picked = e.target.files?.[0] ?? null;
      setFile(picked);
    },
    [],
  );

  const onUpload = useCallback(async () => {
    if (!file) {
      setError("Choose a Burp XML file first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await importFindingsFromBurp(slug, file, includeInfo);
      setResult(res);
      // Surface the new rows immediately — parent hydrates the table.
      onImported(res.imported);
      // Keep the file picked so the analyst can see the result. Clear the
      // input element so a re-pick of the SAME file still triggers onChange.
      if (fileRef.current) fileRef.current.value = "";
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [file, includeInfo, slug, onImported]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Import Burp Pro XML</CardTitle>
        <CardDescription>
          In Burp Pro → <em>Target → Site map</em> (or <em>Dashboard → Issue
          Activity</em>) → select the issues → right-click →{" "}
          <em>Report selected issues</em> → format <em>XML</em>. Save the
          file and drop it here. Each <code>&lt;issue&gt;</code> becomes a{" "}
          <code>vuln_scan</code> finding. Re-importing the same file is a
          no-op — dedup is by Burp <code>&lt;serialNumber&gt;</code>.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <input
            ref={fileRef}
            type="file"
            accept=".xml,application/xml,text/xml"
            onChange={onPick}
            disabled={busy}
            className="text-xs file:mr-3 file:rounded-md file:border file:border-border file:bg-secondary file:px-3 file:py-1.5 file:text-xs file:text-foreground hover:file:bg-secondary/80"
          />
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={includeInfo}
              onChange={(e) => setIncludeInfo(e.target.checked)}
              disabled={busy}
              className="h-3.5 w-3.5 rounded border-border"
            />
            <span>
              Include <code className="text-foreground">Information</code>{" "}
              severity
            </span>
          </label>
          <Button
            type="button"
            onClick={onUpload}
            disabled={!file || busy}
            className="ml-auto"
          >
            <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
            {busy ? "Importing…" : "Import"}
          </Button>
        </div>

        {error && <p className="text-sm text-critical">{error}</p>}

        {result && (
          <div className="rounded-md border border-border bg-secondary/40 p-3 text-xs text-muted-foreground">
            <p className="text-foreground">
              Imported{" "}
              <strong>{result.imported.length}</strong> of{" "}
              <strong>{result.total_items}</strong> issues.
            </p>
            <ul className="mt-1 list-disc pl-5">
              {result.skipped_duplicate > 0 && (
                <li>
                  Skipped <strong>{result.skipped_duplicate}</strong> already
                  imported (same Burp serial).
                </li>
              )}
              {result.skipped_out_of_scope > 0 && (
                <li>
                  Skipped <strong>{result.skipped_out_of_scope}</strong>{" "}
                  out-of-scope host(s).
                </li>
              )}
              {result.skipped_info > 0 && (
                <li>
                  Skipped <strong>{result.skipped_info}</strong> Information
                  severity rows. Toggle the box above to include them.
                </li>
              )}
            </ul>
            {result.export_time && (
              <p className="mt-1 text-muted-foreground/70">
                Burp export stamped{" "}
                {new Date(result.export_time).toLocaleString()}.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
