"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, GitBranch } from "lucide-react";
import { applyEntityReview, previewEntityReview } from "@/lib/api";
import { qk } from "@/lib/hooks";
import type { EntityReviewPreview, EntityReviewTarget } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function EntityReviewDialog({
  slug,
  targets,
  action,
  open,
  onOpenChange,
  onApplied,
}: {
  slug: string;
  targets: EntityReviewTarget[];
  action: "keep" | "exclude";
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApplied: () => void;
}) {
  const qc = useQueryClient();
  const [cascade, setCascade] = useState(false);
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<EntityReviewPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setCascade(false);
    setReason("");
    setPreview(null);
    setError(null);
  }, [action, open, targets]);

  async function loadPreview() {
    setBusy(true);
    setError(null);
    try {
      setPreview(await previewEntityReview(slug, { targets, action, cascade }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!preview || !reason.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await applyEntityReview(slug, {
        targets,
        action,
        cascade,
        reason: reason.trim(),
        preview_sha256: preview.preview_sha256,
        remove_conflicting_exact_includes: false,
      });
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.entities(slug) }),
        qc.invalidateQueries({ queryKey: qk.scope(slug) }),
        qc.invalidateQueries({ queryKey: qk.findings(slug) }),
        qc.invalidateQueries({ queryKey: qk.reportReadiness(slug) }),
      ]);
      onApplied();
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {action === "exclude" ? "Exclude reviewed entities" : "Keep reviewed entities"}
          </DialogTitle>
          <DialogDescription>
            {action === "exclude"
              ? "Preview the authorization and Finding impact before applying exclusions."
              : "Keep marks identities reviewed. It only reverses exclusions previously created by entity review; separately defined scope rules remain authoritative."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <p className="text-sm">
            <strong>{targets.length}</strong> selected {targets.length === 1 ? "entity" : "entities"}
          </p>
          <label className="flex items-start gap-2 rounded-md border border-border p-3 text-sm">
              <input
                type="checkbox"
                checked={cascade}
                onChange={(event) => {
                  setCascade(event.target.checked);
                  setPreview(null);
                }}
                className="mt-1"
              />
              <span>
                <span className="flex items-center gap-1 font-medium">
                  <GitBranch className="h-4 w-4" />
                  {action === "exclude"
                    ? "Cascade to related discoveries"
                    : "Restore the related reviewed branch"}
                </span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  {action === "exclude"
                    ? "Follow findings targeted directly at each selected entity, create exact exclusions for their downstream discoveries, and mark those direct Findings out of scope."
                    : "Follow the same bounded discovery relationships and reverse entity-review-managed exclusions and Finding changes. Independently created exclusions are preserved."}
                </span>
              </span>
            </label>

          <label className="block text-sm font-medium">
            Analyst reason
            <textarea
              aria-label="Entity review reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              maxLength={2_000}
              placeholder={
                action === "exclude"
                  ? "Why is this target and its selected evidence outside the engagement?"
                  : "Why should this identity remain as reviewed engagement evidence?"
              }
              className="mt-1 min-h-20 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
          </label>

          {!preview ? (
            <Button type="button" onClick={() => void loadPreview()} disabled={busy || !reason.trim()}>
              Preview impact
            </Button>
          ) : (
            <section className="space-y-3 rounded-lg border border-border p-4">
              <div className="grid gap-2 sm:grid-cols-3">
                <div><p className="text-xl font-semibold">{preview.entities.length}</p><p className="text-xs text-muted-foreground">Entity dispositions</p></div>
                <div>
                  <p className="text-xl font-semibold">
                    {action === "exclude"
                      ? preview.exclusions_to_create
                      : preview.managed_exclusions_to_remove}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {action === "exclude" ? "Exclusions to create" : "Managed exclusions to restore"}
                  </p>
                </div>
                <div>
                  <p className="text-xl font-semibold">
                    {action === "exclude"
                      ? preview.findings_to_mark_out_of_scope
                      : preview.findings_to_restore}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {action === "exclude" ? "Findings marked out of scope" : "Findings restored"}
                  </p>
                </div>
              </div>
              {preview.truncated && (
                <p className="flex gap-2 text-sm text-destructive">
                  <AlertTriangle className="h-4 w-4 shrink-0" /> Cascade exceeds the 500-entity safety bound. Narrow the selection.
                </p>
              )}
              {preview.exact_include_conflicts > 0 && (
                <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
                  {preview.exact_include_conflicts} exact include {preview.exact_include_conflicts === 1 ? "rule remains" : "rules remain"} recorded but dormant. The managed exclusion takes precedence; restoring this review reactivates the prior include automatically.
                </p>
              )}
              <div className="max-h-56 space-y-2 overflow-y-auto" aria-label="Affected entities">
                {preview.entities.map((entity) => (
                  <div key={`${entity.type}:${entity.value}`} className="rounded-md border border-border/70 p-2 text-xs">
                    <div className="font-mono"><span className="text-muted-foreground">{entity.type}</span> {entity.value}</div>
                    <div className="mt-1 text-muted-foreground">{entity.reason}</div>
                  </div>
                ))}
              </div>
              {preview.findings.length > 0 && (
                <div>
                  <p className="mb-2 text-xs font-medium">
                    Affected Findings ({preview.findings.length})
                  </p>
                  <div className="max-h-48 space-y-2 overflow-y-auto" aria-label="Affected findings">
                    {preview.findings.map((finding) => (
                      <div key={finding.id} className="rounded-md border border-border/70 p-2 text-xs">
                        <p className="font-medium">{finding.title}</p>
                        <p className="mt-1 font-mono text-muted-foreground">
                          {finding.target ?? "No target"} · {finding.current_exclusion ?? "reportable"}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <button
                type="button"
                onClick={() => void loadPreview()}
                disabled={busy}
                className="text-xs text-primary hover:underline disabled:opacity-50"
              >
                Refresh preview
              </button>
            </section>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          {preview && (
            <Button
              type="button"
              onClick={() => void apply()}
              disabled={
                busy ||
                !reason.trim() ||
                preview.truncated
              }
            >
              {action === "exclude" ? "Apply exclusions" : "Mark reviewed"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
