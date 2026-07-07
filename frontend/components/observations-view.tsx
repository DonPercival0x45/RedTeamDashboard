"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  useCreateObservationMutation,
  useDeleteObservationMutation,
  useObservations,
} from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { FindingPhase } from "@/lib/types";

const PHASES: (FindingPhase | "")[] = [
  "",
  "osint",
  "vuln_scan",
  "exploit",
  "phishing",
  "general",
];

const PHASE_LABEL: Record<FindingPhase, string> = {
  osint: "OSINT",
  vuln_scan: "Vuln Scan",
  exploit: "Exploit",
  phishing: "Phishing",
  general: "General",
};

export function ObservationsView({ slug }: { slug: string }) {
  // v1.0.0: react-query owns the fetch. Mutations invalidate/patch the cache;
  // localError catches submit/delete failures separately from the fetch error.
  const { data: observations = [], isLoading, error: queryError } = useObservations(slug);
  const createMutation = useCreateObservationMutation(slug);
  const deleteMutation = useDeleteObservationMutation(slug);

  const [content, setContent] = useState("");
  const [phase, setPhase] = useState<FindingPhase | "">("");
  const [localError, setLocalError] = useState<string | null>(null);
  const error = localError ?? (queryError instanceof Error ? queryError.message : queryError ? String(queryError) : null);
  const submitting = createMutation.isPending;
  const loading = isLoading;

  const handleAdd = async () => {
    if (!content.trim()) return;
    setLocalError(null);
    try {
      await createMutation.mutateAsync({
        content: content.trim(),
        phase: phase || null,
      });
      setContent("");
      setPhase("");
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm("Delete this observation? This cannot be undone.")) {
      return;
    }
    setLocalError(null);
    try {
      await deleteMutation.mutateAsync(id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="space-y-6">
      {/* Add form */}
      <div className="rounded-lg border border-border p-4 space-y-3">
        <h3 className="text-sm font-medium">Add observation</h3>
        <textarea
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring resize-none"
          rows={3}
          placeholder="Anything notable — cert oddities, version strings, interesting headers, recon surface items not yet formal findings…"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
        <div className="flex items-center gap-3">
          <select
            className="rounded-md border border-border bg-background px-2 py-1.5 text-sm text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            value={phase}
            onChange={(e) => setPhase(e.target.value as FindingPhase | "")}
          >
            <option value="">No phase</option>
            {PHASES.filter(Boolean).map((p) => (
              <option key={p} value={p}>
                {PHASE_LABEL[p as FindingPhase]}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            disabled={submitting || !content.trim()}
            onClick={handleAdd}
          >
            {submitting ? "Adding…" : "Add"}
          </Button>
        </div>
      </div>

      {error && <p className="text-sm text-critical">{error}</p>}

      {/* List */}
      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : observations.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No observations yet. Add one above.
        </p>
      ) : (
        <div className="space-y-2">
          {observations.map((obs) => (
            <div
              key={obs.id}
              className="flex items-start gap-3 rounded-lg border border-border px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                {obs.phase && (
                  <span
                    className={cn(
                      "mb-1.5 inline-block rounded-full border px-2 py-0.5 text-xs text-muted-foreground",
                      "border-border",
                    )}
                  >
                    {PHASE_LABEL[obs.phase]}
                  </span>
                )}
                <p className="text-sm">{obs.content}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {obs.created_at.slice(0, 16).replace("T", " ")} UTC
                </p>
              </div>
              <button
                type="button"
                onClick={() => handleDelete(obs.id)}
                className="shrink-0 text-muted-foreground hover:text-critical"
                aria-label="Delete observation"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
