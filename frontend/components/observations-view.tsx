"use client";

import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { createObservation, deleteObservation, listObservations } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { FindingPhase, Observation } from "@/lib/types";

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
  const [observations, setObservations] = useState<Observation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [content, setContent] = useState("");
  const [phase, setPhase] = useState<FindingPhase | "">("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setLoading(true);
    listObservations(slug)
      .then(setObservations)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [slug]);

  const handleAdd = async () => {
    if (!content.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const obs = await createObservation(slug, {
        content: content.trim(),
        phase: phase || null,
      });
      setObservations((prev) => [...prev, obs]);
      setContent("");
      setPhase("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteObservation(id);
      setObservations((prev) => prev.filter((o) => o.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
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
