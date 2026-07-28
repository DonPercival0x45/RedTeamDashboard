"use client";

import { useState } from "react";

import { createFinding } from "@/lib/api";
import type { Finding, FindingPhase, Severity } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

const SEVERITIES: Severity[] = ["info", "low", "medium", "high", "critical"];
const PHASES: Array<{ value: FindingPhase; label: string }> = [
  { value: "general", label: "General" },
  { value: "osint", label: "OSINT" },
  { value: "vuln_scan", label: "Vulnerability scan" },
  { value: "exploit", label: "Exploit" },
  { value: "phishing", label: "Phishing" },
];

export function FindingCreateDialog({
  slug,
  onClose,
  onCreated,
}: {
  slug: string;
  onClose: () => void;
  onCreated: (finding: Finding) => void | Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [target, setTarget] = useState("");
  const [severity, setSeverity] = useState<Severity>("info");
  const [phase, setPhase] = useState<FindingPhase>("general");
  const [observedAt, setObservedAt] = useState("");
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = title.trim().length > 0 && !busy;
  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const finding = await createFinding(slug, {
        title: title.trim(),
        summary: summary.trim() || null,
        severity,
        phase,
        target: target.trim() || null,
        observed_at: observedAt
          ? new Date(`${observedAt}T12:00:00Z`).toISOString()
          : null,
        tags: Array.from(
          new Set(
            tags
              .split(",")
              .map((value) => value.trim())
              .filter(Boolean),
          ),
        ),
      });
      await onCreated(finding);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Create custom Finding</DialogTitle>
          <DialogDescription>
            Record an analyst-observed issue that tooling did not surface. It remains an
            independent Finding with its own validation and reporting lifecycle.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <label className="space-y-1.5 text-sm">
            <span>Title *</span>
            <Input
              autoFocus
              value={title}
              maxLength={300}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Reflected XSS in /search endpoint"
            />
          </label>
          <div className="space-y-1.5">
            <Label htmlFor="custom-finding-details">Details</Label>
            <Textarea
              id="custom-finding-details"
              value={summary}
              rows={5}
              onChange={(event) => setSummary(event.target.value)}
              placeholder="Observation, impact, supporting evidence, and reproduction notes."
            />
          </div>
          <label className="space-y-1.5 text-sm">
            <span>Affected target</span>
            <Input
              value={target}
              maxLength={500}
              onChange={(event) => setTarget(event.target.value)}
              placeholder="Host, URL, IP, mailbox, or other affected entity"
              className="font-mono text-xs"
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1.5 text-sm">
              <span>Severity</span>
              <select
                value={severity}
                onChange={(event) => setSeverity(event.target.value as Severity)}
                className="h-10 w-full rounded-md border border-input bg-background px-3"
              >
                {SEVERITIES.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1.5 text-sm">
              <span>Phase</span>
              <select
                value={phase}
                onChange={(event) => setPhase(event.target.value as FindingPhase)}
                className="h-10 w-full rounded-md border border-input bg-background px-3"
              >
                {PHASES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="space-y-1.5 text-sm">
            <span>Observed on</span>
            <Input
              type="date"
              value={observedAt}
              onChange={(event) => setObservedAt(event.target.value)}
            />
          </label>
          <label className="space-y-1.5 text-sm">
            <span>Tags</span>
            <Input
              value={tags}
              onChange={(event) => setTags(event.target.value)}
              placeholder="web, authentication, client-review"
            />
            <span className="block text-xs text-muted-foreground">
              Optional comma-separated analyst tags.
            </span>
          </label>
          {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="button" onClick={() => void submit()} disabled={!canSubmit}>
            {busy ? "Creating…" : "Create Finding"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
