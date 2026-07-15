"use client";

// v2.4.0 — Report Builder body. This is the layout that used to render
// as the per-engagement Report tab; it now lives inside
// Automation → Reporting with the engagement picked upstream. Same UX
// as before (readiness preflight + blocker/warning cards + Client vs.
// Internal profile radio + JSON export + PDF download); only the mount
// point moved so an analyst can pick which engagement to report on
// without navigating into it.

import Link from "next/link";
import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DownloadReport } from "@/components/download-report";
import { downloadEngagementExport } from "@/lib/api";
import { useReportReadiness } from "@/lib/hooks";

export function ReportBuilder({ slug }: { slug: string }) {
  const readinessQuery = useReportReadiness(slug);
  const readiness = readinessQuery.data;
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportProfile, setExportProfile] = useState<"client" | "internal">(
    "client",
  );
  const omitExcluded = exportProfile === "client";

  const onExportJSON = async () => {
    setExportBusy(true);
    setExportError(null);
    try {
      await downloadEngagementExport(slug, { omitExcluded });
    } catch (err) {
      setExportError(err instanceof Error ? err.message : String(err));
    } finally {
      setExportBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle>Report</CardTitle>
        <div className="flex gap-2">
          <div className="flex flex-col items-end gap-1">
            <button
              type="button"
              onClick={onExportJSON}
              disabled={exportBusy}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-xs hover:bg-secondary disabled:opacity-50"
            >
              {exportBusy
                ? "Exporting…"
                : exportProfile === "client"
                  ? "Export client JSON"
                  : "Export internal JSON"}
            </button>
            {exportError && (
              <p className="text-xs text-destructive">{exportError}</p>
            )}
          </div>
          <DownloadReport slug={slug} omitExcluded={omitExcluded} />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <section className="rounded-lg border border-border bg-background/40 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span
                  className={`h-2.5 w-2.5 rounded-full ${readiness?.ready ? "bg-emerald-500" : "bg-amber-500"}`}
                />
                <h3 className="text-sm font-semibold">
                  {readinessQuery.isLoading
                    ? "Checking report readiness…"
                    : readiness?.ready
                      ? "Ready for client review"
                      : "Report needs attention"}
                </h3>
              </div>
              {readiness && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {readiness.reportable_count} reportable ·{" "}
                  {readiness.total_findings} total findings
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={() => void readinessQuery.refetch()}
              className="text-xs text-muted-foreground hover:text-foreground hover:underline"
            >
              Refresh preflight
            </button>
          </div>

          {readinessQuery.error && (
            <p className="mt-3 text-xs text-destructive">
              Could not load report readiness.
            </p>
          )}
          {readiness && (
            <ul className="mt-3 grid gap-2 sm:grid-cols-2">
              {readiness.checks
                .filter((check) => check.count > 0)
                .map((check) => {
                  // v2.4.0: Review links used to jump to a sibling engagement
                  // view; now that Report lives on the Automation page and
                  // Report/Tools tabs are gone, link into the engagement
                  // workbench view instead (only ones that still exist).
                  const view =
                    check.target_view?.split("&", 1)[0] ?? "findings";
                  const tone =
                    check.level === "blocker"
                      ? "border-rose-500/40 bg-rose-500/10"
                      : check.level === "warning"
                        ? "border-amber-500/40 bg-amber-500/10"
                        : "border-sky-500/40 bg-sky-500/10";
                  return (
                    <li
                      key={check.key}
                      className={`rounded-md border p-3 ${tone}`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                            {check.level}
                          </p>
                          <p className="mt-1 text-xs">{check.message}</p>
                        </div>
                        {check.target_view && (
                          <Link
                            href={`/e?slug=${encodeURIComponent(slug)}&view=${encodeURIComponent(view)}`}
                            className="shrink-0 text-xs underline"
                          >
                            Review
                          </Link>
                        )}
                      </div>
                    </li>
                  );
                })}
            </ul>
          )}
          {readiness?.ready && (
            <p className="mt-3 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-700 dark:text-emerald-200">
              No report blockers remain. Warnings are advisory and exports
              remain analyst controlled.
            </p>
          )}
        </section>

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Export profile</legend>
          <label className="flex cursor-pointer items-start gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/5 p-3 text-sm">
            <input
              type="radio"
              name="export-profile"
              value="client"
              checked={exportProfile === "client"}
              onChange={() => setExportProfile("client")}
              className="mt-0.5 cursor-pointer accent-emerald-600"
            />
            <div>
              <span className="font-medium">Client deliverable</span>
              <span className="ml-2 rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-[10px] uppercase text-emerald-700 dark:text-emerald-300">
                Recommended
              </span>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Excludes findings marked <em>Out of scope</em> or{" "}
                <em>Outside ROE</em>. This is the default for both PDF and JSON
                downloads.
              </p>
            </div>
          </label>
          <label className="flex cursor-pointer items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
            <input
              type="radio"
              name="export-profile"
              value="internal"
              checked={exportProfile === "internal"}
              onChange={() => setExportProfile("internal")}
              className="mt-0.5 cursor-pointer accent-amber-600"
            />
            <div>
              <span className="font-medium">Internal full record</span>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Includes excluded findings for audit and archival use. Do not
                send this profile to the client without reviewing the contents.
              </p>
            </div>
          </label>
        </fieldset>
        <p className="text-xs text-muted-foreground/70">
          PDF includes <strong>validated</strong> findings across every phase.
          JSON remains a full engagement snapshot of all validation states; the
          selected profile controls whether excluded findings are present.
          Filenames identify the profile.
        </p>
      </CardContent>
    </Card>
  );
}
