"use client";

// v1.26.0 — per-key model selection modal.
//
// Opens from the "Select" button on each row in ProviderKeyList. Shows
// the full discovered catalog from a fresh probe (or the currently
// stored ``models[]`` as a fallback if the probe fails). Currently
// stored models are pre-checked. On Save we PATCH the key with the
// checked subset — this is what the Configurations dropdown reads from.
//
// The point is curation: an OpenAI key surfaces 40+ models on probe,
// and the analyst only wants a handful in the Configurations picker.
// This modal is where they trim the list.
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { AlertCircle, Loader2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { probeSavedProviderKey, updateProviderKey } from "@/lib/api";
import type { ProviderKey } from "@/lib/types";
import { cn } from "@/lib/utils";

export function SelectModelsModal({
  keyRow,
  open,
  onOpenChange,
  onSaved,
}: {
  keyRow: ProviderKey | null;
  open: boolean;
  onOpenChange: (next: boolean) => void;
  onSaved: () => void | Promise<void>;
}) {
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [discovered, setDiscovered] = useState<string[]>([]);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Reprobe every time the modal opens against a key. Keeps the picker
  // in sync with what the provider actually serves right now — a stale
  // list from a previous session could hide freshly-released models.
  useEffect(() => {
    let cancelled = false;
    if (!open || !keyRow) return;
    setProbing(true);
    setProbeError(null);
    setSaveError(null);
    setChecked(new Set(keyRow.models));
    (async () => {
      try {
        const result = await probeSavedProviderKey(keyRow.id);
        if (cancelled) return;
        if (result.ok && result.models.length > 0) {
          // Union: probe results first (freshest), then any models
          // already stored on the key that the probe didn't surface
          // (custom aliases, revoked-but-still-configured, etc.).
          const set = new Set<string>();
          const merged: string[] = [];
          for (const m of [...result.models, ...keyRow.models]) {
            if (m && !set.has(m)) {
              set.add(m);
              merged.push(m);
            }
          }
          setDiscovered(merged);
        } else {
          // Probe failed — fall back to whatever's already stored so
          // the analyst can still edit. Missing models can be added
          // manually later.
          setDiscovered([...keyRow.models]);
          if (!result.ok) {
            setProbeError(
              result.error ||
                (result.reachable
                  ? "reachable but rejected"
                  : "provider unreachable"),
            );
          }
        }
      } catch (err) {
        if (!cancelled) {
          setDiscovered([...keyRow.models]);
          setProbeError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!cancelled) setProbing(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, keyRow]);

  const sortedDiscovered = useMemo(
    () => [...discovered].sort((a, b) => a.localeCompare(b)),
    [discovered],
  );

  const toggle = (m: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(m)) next.delete(m);
      else next.add(m);
      return next;
    });
  };

  const selectAll = () => setChecked(new Set(sortedDiscovered));
  const selectNone = () => setChecked(new Set());

  const onSave = async () => {
    if (!keyRow) return;
    setSaving(true);
    setSaveError(null);
    try {
      await updateProviderKey(keyRow.id, {
        models: sortedDiscovered.filter((m) => checked.has(m)),
      });
      await onSaved();
      onOpenChange(false);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          aria-label="Select models"
          className={cn(
            "fixed left-[50%] top-[50%] z-50 flex max-h-[85vh] w-[95vw] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-lg border border-border bg-background shadow-2xl",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-3">
            <div>
              <h2 className="text-base font-semibold">Select models</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Pick which of{" "}
                <span className="font-mono">{keyRow?.name}</span>&apos;s
                discovered models appear in the Configurations dropdowns.
                Uncheck to hide.
              </p>
            </div>
            <DialogPrimitive.Close
              aria-label="Close"
              className="shrink-0 rounded-sm p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </DialogPrimitive.Close>
          </header>

          <div className="min-h-0 flex-1 overflow-auto px-5 py-3">
            {probing && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Probing {keyRow?.provider} for the live model catalog…
              </div>
            )}

            {!probing && probeError && (
              <div className="mb-3 flex items-start gap-2 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-900 dark:text-amber-200">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  Probe failed: {probeError}. Showing the models already
                  stored on this key.
                </span>
              </div>
            )}

            {!probing && sortedDiscovered.length === 0 && (
              <p className="rounded border border-dashed border-border bg-muted/40 px-4 py-6 text-center text-sm text-muted-foreground">
                No models discovered for this key. Try Test / re-upload.
              </p>
            )}

            {!probing && sortedDiscovered.length > 0 && (
              <>
                <div className="mb-2 flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">
                    {checked.size} of {sortedDiscovered.length} selected
                  </span>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={selectAll}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      All
                    </button>
                    <span className="text-muted-foreground/40">·</span>
                    <button
                      type="button"
                      onClick={selectNone}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      None
                    </button>
                  </div>
                </div>
                <ul className="divide-y divide-border/60 rounded border border-border">
                  {sortedDiscovered.map((m) => (
                    <li key={m}>
                      <label className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-muted/40">
                        <input
                          type="checkbox"
                          checked={checked.has(m)}
                          onChange={() => toggle(m)}
                          className="h-4 w-4 rounded border-border accent-primary"
                        />
                        <span className="font-mono text-sm">{m}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          <footer className="flex items-center justify-between gap-3 border-t border-border px-5 py-3">
            {saveError ? (
              <span className="text-xs text-critical">{saveError}</span>
            ) : (
              <span className="text-xs text-muted-foreground">
                Only checked models appear in Configurations dropdowns.
              </span>
            )}
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => onOpenChange(false)}
                disabled={saving}
              >
                Cancel
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={onSave}
                disabled={saving || probing}
              >
                {saving ? "Saving…" : `Save (${checked.size})`}
              </Button>
            </div>
          </footer>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
