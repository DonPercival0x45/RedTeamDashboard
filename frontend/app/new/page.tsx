"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
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
import { DatePicker } from "@/components/ui/date-picker";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ScopeImporter } from "@/components/scope-importer";
import { createEngagement } from "@/lib/api";
import type { EngagementTimeFrame, ScopeKind } from "@/lib/types";

const KINDS: ScopeKind[] = ["domain", "cidr", "ip", "url"];

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
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const maxScheduleDate = useMemo(() => {
    const d = new Date();
    d.setFullYear(d.getFullYear() + 3);
    return d;
  }, []);
  const maxScheduleDateStr = useMemo(() => {
    const y = maxScheduleDate.getFullYear();
    const m = String(maxScheduleDate.getMonth() + 1).padStart(2, "0");
    const day = String(maxScheduleDate.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }, [maxScheduleDate]);
  const startDateAsDate = useMemo(() => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(startDate);
    if (!match) return undefined;
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  }, [startDate]);

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
    setScope((items) => [...items, { kind, value: candidate, isExclusion }]);
    setValue("");
    setIsExclusion(false);
    setError(null);
  };

  const submit = async () => {
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (endDate && !startDate) {
      setError("Choose a start date before adding an end date.");
      return;
    }
    if (endDate && endDate < startDate) {
      setError("End date can't be before start date.");
      return;
    }
    if (startDate > maxScheduleDateStr || (endDate && endDate > maxScheduleDateStr)) {
      setError("Planning dates can't be more than 3 years in the future.");
      return;
    }

    const timeFrame: EngagementTimeFrame = endDate ? "custom" : "point_in_time";
    setBusy(true);
    setError(null);
    try {
      const eng = await createEngagement({
        name: name.trim(),
        description: description.trim() || undefined,
        time_frame: timeFrame,
        start_date: startDate || null,
        end_date: endDate || null,
        initial_scope: scope.map((item) => ({
          kind: item.kind,
          value: item.value,
          is_exclusion: item.isExclusion,
        })),
      });
      const nextView = scope.length > 0 ? "strategy&setup=initial-guidance" : "scope";
      router.push(`/e?slug=${encodeURIComponent(eng.slug)}&view=${nextView}`);
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
        <Link href="/" className="text-xs text-muted-foreground hover:text-foreground">
          ← all engagements
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">New engagement</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          A name and scope are enough to get started. You can add planning details now or later.
        </p>
      </div>

      <Card>
        <CardHeader><CardTitle className="text-base">Details</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">Name</Label>
            <Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme assessment" required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="description">Description / rules of engagement (optional)</Label>
            <Textarea id="description" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} placeholder="Objectives, constraints, point of contact…" />
          </div>
        </CardContent>
      </Card>

      <details className="rounded-lg border border-border bg-card">
        <summary className="cursor-pointer px-6 py-4 text-sm font-medium">
          Planning dates <span className="font-normal text-muted-foreground">(optional)</span>
        </summary>
        <div className="space-y-4 border-t border-border px-6 py-4">
          <p className="text-xs text-muted-foreground">
            These dates are planning metadata only. They do not schedule or block runs.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="start_date">Start date</Label>
              <DatePicker id="start_date" value={startDate} onChange={setStartDate} maxDate={maxScheduleDate} placeholder="Pick a start date" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="end_date">End date</Label>
              <DatePicker id="end_date" value={endDate} onChange={setEndDate} minDate={startDateAsDate} maxDate={maxScheduleDate} placeholder="Pick an end date" />
            </div>
          </div>
        </div>
      </details>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Scope</CardTitle>
          <CardDescription>
            Targets the engagement may touch. Tool calls outside scope are denied by the gate. Add includes and optional exclusions.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ScopeImporter
            onCommit={(_text, preview) => {
              const seen = new Set(scope.map((item) => `${item.kind}\u0000${item.value}\u0000${item.isExclusion}`));
              const additions: ScopeDraft[] = [];
              const duplicates: ScopeDraft[] = [];
              for (const row of preview.preview) {
                const draft = { kind: row.kind, value: row.value, isExclusion: row.is_exclusion };
                const key = `${draft.kind}\u0000${draft.value}\u0000${draft.isExclusion}`;
                if (seen.has(key)) duplicates.push(draft);
                else { seen.add(key); additions.push(draft); }
              }
              setScope((items) => [...items, ...additions]);
              if (duplicates.length > 0) {
                const first = duplicates[0];
                setError(`${first.kind}:${first.value} is already staged${duplicates.length > 1 ? `; ${duplicates.length} duplicate entries were skipped` : ""}.`);
              } else setError(null);
            }}
          />
          <div className="grid gap-3 sm:grid-cols-[7rem_1fr_auto] sm:items-end">
            <div className="space-y-2">
              <Label htmlFor="kind">Kind</Label>
              <select id="kind" value={kind} onChange={(e) => setKind(e.target.value as ScopeKind)} className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
                {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="value">Value</Label>
              <Input id="value" value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addScope(); } }} placeholder={placeholder} />
            </div>
            <Button type="button" variant="outline" onClick={addScope}>Add</Button>
            <label className="flex items-center gap-2 text-sm sm:col-span-3">
              <input type="checkbox" checked={isExclusion} onChange={(e) => setIsExclusion(e.target.checked)} className="h-4 w-4 rounded border-input" />
              Exclusion (carve out from a broader include)
            </label>
          </div>

          {scope.length === 0 ? (
            <p className="text-sm text-muted-foreground">No scope yet — save now to continue on the Scope tab.</p>
          ) : (
            <ul className="divide-y divide-border">
              {scope.map((item, i) => (
                <li key={`${item.kind}-${item.value}-${i}`} className="flex items-center justify-between py-2">
                  <div className="flex items-center gap-3">
                    <Badge variant={item.isExclusion ? "destructive" : "secondary"}>{item.kind}{item.isExclusion ? " · exclude" : ""}</Badge>
                    <span className="font-mono text-sm">{item.value}</span>
                  </div>
                  <Button type="button" variant="ghost" size="icon" onClick={() => setScope((items) => items.filter((_, j) => j !== i))} aria-label="Remove scope item"><Trash2 className="h-4 w-4" /></Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {error && <p className="text-sm text-critical">{error}</p>}
      <div className="flex justify-end">
        <Button disabled={busy} onClick={submit}>{busy ? "Saving…" : scope.length > 0 ? "Save and continue to Strategy" : "Save engagement"}</Button>
      </div>
    </div>
  );
}
