"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ScopeImporter } from "@/components/scope-importer";
import { createEngagement } from "@/lib/api";
import type { EngagementTimeFrame, ScopeKind } from "@/lib/types";

// Nessus-style engagement setup (CHARTER Idea 3): name, details, time frame,
// and scope. v0.6.0 removed the kickoff-on-create button — engagement creation
// is now a preset. The analyst launches scans from the Scope tab once the
// engagement exists.

const KINDS: ScopeKind[] = ["domain", "cidr", "ip", "url"];

const TIME_FRAMES: { value: EngagementTimeFrame; label: string; hint: string }[] = [
  {
    value: "point_in_time",
    label: "Point in time",
    hint: "Single one-shot pass. No recurring scans planned.",
  },
  {
    value: "point_in_time_continuous",
    label: "Point in time, continuous",
    hint: "One window, but stays open for follow-up scans as findings accrue.",
  },
  {
    value: "repeatable",
    label: "Repeatable",
    hint: "Ongoing engagement that re-runs on the analyst's cadence.",
  },
  {
    value: "custom",
    label: "Custom window",
    hint: "Fixed start and end dates.",
  },
];

interface ScopeDraft {
  kind: ScopeKind;
  value: string;
  isExclusion: boolean;
}

export default function NewEngagementPage() {
  const router = useRouter();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState<ScopeDraft[]>([]);

  const [kind, setKind] = useState<ScopeKind>("domain");
  const [value, setValue] = useState("");
  const [isExclusion, setIsExclusion] = useState(false);

  const [timeFrame, setTimeFrame] = useState<EngagementTimeFrame>("point_in_time");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addScope = () => {
    const candidate = value.trim();
    if (!candidate) return;
    const duplicate = scope.some(
      (item) =>
        item.kind === kind &&
        item.value === candidate &&
        item.isExclusion === isExclusion,
    );
    if (duplicate) {
      setError(
        `${kind}:${candidate} is already staged as ${isExclusion ? "an exclusion" : "an included target"}.`,
      );
      return;
    }
    setScope((items) => [
      ...items,
      { kind, value: candidate, isExclusion },
    ]);
    setValue("");
    setIsExclusion(false);
    setError(null);
  };

  const submit = async () => {
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (timeFrame === "custom") {
      if (!startDate || !endDate) {
        setError("Custom window requires both start and end dates.");
        return;
      }
      if (endDate < startDate) {
        setError("End date can't be before start date.");
        return;
      }
    }
    setBusy(true);
    setError(null);
    try {
      const eng = await createEngagement({
        name: name.trim(),
        description: description.trim() || undefined,
        time_frame: timeFrame,
        start_date: timeFrame === "custom" ? startDate : null,
        end_date: timeFrame === "custom" ? endDate : null,
        initial_scope: scope.map((item) => ({
          kind: item.kind,
          value: item.value,
          is_exclusion: item.isExclusion,
        })),
      });
      router.push(`/e?slug=${encodeURIComponent(eng.slug)}&view=scope`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  const placeholder =
    kind === "domain"
      ? "acme.com"
      : kind === "cidr"
        ? "10.0.0.0/24"
        : kind === "ip"
          ? "10.0.0.5"
          : "https://acme.com/login";

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← all engagements
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          New engagement
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Name it, set the time frame, and stage scope. Launch scans from the
          Scope tab once the engagement is created.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Acme Q1 Pentest"
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="description">Description / rules of engagement</Label>
            <Textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="Objectives, constraints, point of contact…"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Time frame</CardTitle>
          <CardDescription>
            How the engagement is scheduled. Metadata only — used in the
            engagement header and report.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="time_frame">Type</Label>
            <select
              id="time_frame"
              value={timeFrame}
              onChange={(e) => setTimeFrame(e.target.value as EngagementTimeFrame)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              {TIME_FRAMES.map((tf) => (
                <option key={tf.value} value={tf.value}>
                  {tf.label}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              {TIME_FRAMES.find((tf) => tf.value === timeFrame)?.hint}
            </p>
          </div>
          {timeFrame === "custom" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="start_date">Start date</Label>
                <Input
                  id="start_date"
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="end_date">End date</Label>
                <Input
                  id="end_date"
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  required
                />
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Scope</CardTitle>
          <CardDescription>
            Targets the engagement may touch. Tool calls outside scope are denied
            by the gate. Add includes (and optional exclusions).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ScopeImporter
            onCommit={(_text, preview) => {
              const seen = new Set(
                scope.map(
                  (item) => `${item.kind}\u0000${item.value}\u0000${item.isExclusion}`,
                ),
              );
              const additions: ScopeDraft[] = [];
              const duplicates: ScopeDraft[] = [];
              for (const row of preview.preview) {
                const draft = {
                  kind: row.kind,
                  value: row.value,
                  isExclusion: row.is_exclusion,
                };
                const key = `${draft.kind}\u0000${draft.value}\u0000${draft.isExclusion}`;
                if (seen.has(key)) {
                  duplicates.push(draft);
                } else {
                  seen.add(key);
                  additions.push(draft);
                }
              }
              setScope((items) => [...items, ...additions]);
              if (duplicates.length > 0) {
                const first = duplicates[0];
                setError(
                  `${first.kind}:${first.value} is already staged${duplicates.length > 1 ? `; ${duplicates.length} duplicate entries were skipped` : ""}.`,
                );
              } else {
                setError(null);
              }
            }}
          />
          <div className="grid gap-3 sm:grid-cols-[7rem_1fr_auto] sm:items-end">
            <div className="space-y-2">
              <Label htmlFor="kind">Kind</Label>
              <select
                id="kind"
                value={kind}
                onChange={(e) => setKind(e.target.value as ScopeKind)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="value">Value</Label>
              <Input
                id="value"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addScope();
                  }
                }}
                placeholder={placeholder}
              />
            </div>
            <Button type="button" variant="outline" onClick={addScope}>
              Add
            </Button>
            <label className="flex items-center gap-2 text-sm sm:col-span-3">
              <input
                type="checkbox"
                checked={isExclusion}
                onChange={(e) => setIsExclusion(e.target.checked)}
                className="h-4 w-4 rounded border-input"
              />
              Exclusion (carve out from a broader include)
            </label>
          </div>

          {scope.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No scope yet — add includes here or from the Scope tab after save.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {scope.map((item, i) => (
                <li
                  key={`${item.kind}-${item.value}-${i}`}
                  className="flex items-center justify-between py-2"
                >
                  <div className="flex items-center gap-3">
                    <Badge variant={item.isExclusion ? "destructive" : "secondary"}>
                      {item.kind}
                      {item.isExclusion ? " · exclude" : ""}
                    </Badge>
                    <span className="font-mono text-sm">{item.value}</span>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => setScope((s) => s.filter((_, j) => j !== i))}
                    aria-label="Remove scope item"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {error && <p className="text-sm text-critical">{error}</p>}

      <div className="flex justify-end">
        <Button disabled={busy} onClick={submit}>
          {busy ? "Saving…" : "Save engagement"}
        </Button>
      </div>
    </div>
  );
}
