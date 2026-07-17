"use client";

import { useState } from "react";
import { RefreshCw, Copy, Check, Download } from "lucide-react";
import { useDiagnostics } from "@/lib/hooks";
import { Button } from "@/components/ui/button";

// Copy-pastable runtime triage dump for an engagement. Pulls the full activity
// (runs + errors, audit log, record counts) as a single markdown blob so an
// agent or analyst can paste it straight into a prompt / ticket to diagnose
// what's happening behind the scenes.
export function DiagnosticsView({ slug }: { slug: string }) {
  const { data, isLoading, isError, error, refetch, isFetching } = useDiagnostics(slug);
  const [copied, setCopied] = useState(false);

  const markdown = data?.markdown ?? "";

  const copy = async () => {
    if (!markdown) return;
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // clipboard may be blocked (permissions / non-secure context) — fall back
      // to a transient selection of the <pre> so the user can Cmd-C manually.
      const pre = document.getElementById("diagnostics-pre");
      if (pre) {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
      }
    }
  };

  const download = () => {
    if (!markdown) return;
    const blob = new Blob([markdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `diagnostics-${slug}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="mx-auto max-w-5xl space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Diagnostics</h2>
          <p className="text-xs text-muted-foreground">
            Full engagement activity as copy-pastable markdown — runs, errors, audit log, counts.
            {data?.generated_at && (
              <> · generated <span className="font-mono">{data.generated_at.replace("T", " ").replace("Z", " UTC")}</span></>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={download} disabled={!markdown}>
            <Download className="mr-1.5 h-3.5 w-3.5" />
            .md
          </Button>
          <Button size="sm" onClick={copy} disabled={!markdown}>
            {copied ? <Check className="mr-1.5 h-3.5 w-3.5" /> : <Copy className="mr-1.5 h-3.5 w-3.5" />}
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </div>

      {isLoading && (
        <div className="rounded-lg border border-border p-8 text-center text-sm text-muted-foreground">
          Building diagnostics dump…
        </div>
      )}
      {isError && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          Failed to load diagnostics: {(error as Error)?.message ?? "unknown error"}
        </div>
      )}
      {markdown && (
        <pre
          id="diagnostics-pre"
          className="max-h-[calc(100vh-13rem)] overflow-auto rounded-lg border border-border bg-muted/30 p-4 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words"
        >
          {markdown}
        </pre>
      )}
    </div>
  );
}
