"use client";

import { useState } from "react";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { downloadEngagementReport } from "@/lib/api";
import { useSources } from "@/lib/source-context";

export function DownloadReport({ slug }: { slug: string }) {
  const { current } = useSources();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onClick = async () => {
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      await downloadEngagementReport(current, slug);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        variant="outline"
        size="sm"
        onClick={onClick}
        disabled={busy || !current}
      >
        <Download className="mr-2 h-4 w-4" />
        {busy ? "Generating…" : "Download report"}
      </Button>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
