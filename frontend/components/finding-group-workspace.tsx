"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { FolderPlus, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { FindingCreateDialog } from "@/components/finding-create-dialog";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Badge } from "@/components/ui/badge";
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
import { Textarea } from "@/components/ui/textarea";
import {
  createFindingGroup,
  deleteFindingGroup,
  updateFindingGroup,
} from "@/lib/api";
import { qk, useFindingGroups } from "@/lib/hooks";
import type { Finding, FindingGroup, Severity } from "@/lib/types";
import { cn } from "@/lib/utils";

const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "border-rose-500/50 bg-rose-500/10 text-rose-700 dark:text-rose-200",
  high: "border-pink-500/50 bg-pink-500/10 text-pink-700 dark:text-pink-200",
  medium: "border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-200",
  low: "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
  info: "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-200",
};

type GroupStep = "details" | "members" | "review";

function newDraftUuid(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function FindingGroupDialog({
  slug,
  findings,
  group,
  onClose,
  onSaved,
}: {
  slug: string;
  findings: Finding[];
  group: FindingGroup | null;
  onClose: () => void;
  onSaved: (group: FindingGroup) => void;
}) {
  const qc = useQueryClient();
  const initialIds = group?.members.map((member) => member.finding_id) ?? [];
  const [step, setStep] = useState<GroupStep>("details");
  const [name, setName] = useState(group?.name ?? "");
  const [rationale, setRationale] = useState(group?.rationale ?? "");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set(initialIds));
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleConflict, setStaleConflict] = useState(false);
  const idempotencyKey = useMemo(newDraftUuid, []);
  const findingById = useMemo(() => {
    const map = new Map(findings.map((finding) => [finding.id, finding]));
    for (const member of group?.members ?? []) map.set(member.finding_id, member.finding);
    return map;
  }, [findings, group?.members]);
  const candidates = useMemo(() => {
    const query = search.trim().toLowerCase();
    return Array.from(findingById.values())
      .filter(
        (finding) =>
          !query ||
          finding.title.toLowerCase().includes(query) ||
          finding.id.toLowerCase().includes(query) ||
          finding.target?.toLowerCase().includes(query) ||
          finding.tool?.toLowerCase().includes(query),
      )
      .sort((left, right) => right.created_at.localeCompare(left.created_at));
  }, [findingById, search]);
  const selected = Array.from(selectedIds)
    .map((id) => findingById.get(id))
    .filter((finding): finding is Finding => finding !== undefined);
  const unavailableIds = new Set(
    (group?.members ?? [])
      .filter((member) => !member.available)
      .map((member) => member.finding_id),
  );
  const detailsReady = name.trim().length > 0 && rationale.trim().length > 0;
  const membersReady = selectedIds.size >= 2 && selectedIds.size <= 200 && !Array.from(selectedIds).some((id) => unavailableIds.has(id));

  const toggle = (id: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else if (next.size < 200) next.add(id);
      return next;
    });
    setError(null);
  };

  const save = async () => {
    if (!detailsReady || !membersReady || busy) return;
    setBusy(true);
    setError(null);
    setStaleConflict(false);
    try {
      const findingIds = selected.map((finding) => finding.id);
      const saved = group
        ? await updateFindingGroup(slug, group.id, {
            expected_row_version: group.row_version,
            name: name.trim(),
            rationale: rationale.trim(),
            finding_ids: findingIds,
          })
        : await createFindingGroup(slug, {
            name: name.trim(),
            rationale: rationale.trim(),
            finding_ids: findingIds,
            idempotency_key: idempotencyKey,
          });
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.findingGroups(slug) }),
        qc.invalidateQueries({ queryKey: qk.findings(slug) }),
        qc.invalidateQueries({ queryKey: qk.findingHierarchy(slug) }),
      ]);
      onSaved(saved);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      setError(message);
      setStaleConflict(message.toLowerCase().includes("changed since"));
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="max-h-[92vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{group ? "Edit custom group" : "Create custom group"}</DialogTitle>
          <DialogDescription>
            Presentation only: member Findings retain their IDs, evidence, validation,
            reporting, remediation, history, and scope state.
          </DialogDescription>
        </DialogHeader>
        <div className="flex gap-1 rounded-md border border-border bg-muted/30 p-1 text-xs">
          {(["details", "members", "review"] as GroupStep[]).map((value, index) => {
            const disabled =
              (value === "members" && !detailsReady) ||
              (value === "review" && (!detailsReady || !membersReady));
            return (
              <button
                key={value}
                type="button"
                disabled={disabled}
                aria-current={step === value ? "step" : undefined}
                onClick={() => setStep(value)}
                className={cn(
                  "flex-1 rounded px-2 py-1.5 capitalize disabled:cursor-not-allowed disabled:opacity-50",
                  step === value ? "bg-background font-medium shadow-sm" : "text-muted-foreground",
                )}
              >
                {index + 1}. {value}
              </button>
            );
          })}
        </div>

        {step === "details" ? (
          <div className="space-y-4">
            <label className="space-y-1.5 text-sm">
              <span>Group name *</span>
              <Input autoFocus maxLength={200} value={name} onChange={(event) => setName(event.target.value)} placeholder="Internet-facing authentication weaknesses" />
            </label>
            <label className="space-y-1.5 text-sm">
              <span>Grouping rationale *</span>
              <Textarea maxLength={4000} rows={5} value={rationale} onChange={(event) => setRationale(event.target.value)} placeholder="Explain why these independent Findings belong in the same analyst view." />
            </label>
          </div>
        ) : null}

        {step === "members" ? (
          <div className="space-y-3">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input aria-label="Search Findings for group" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search titles, targets, tools, or IDs" className="pl-9" />
            </div>
            <p className="text-xs text-muted-foreground">Select 2–200 source Findings. A Finding may belong to multiple custom groups.</p>
            <div className="max-h-[48vh] space-y-1 overflow-y-auto rounded-md border border-border p-2">
              {candidates.length === 0 ? <p className="p-3 text-sm text-muted-foreground">No Findings match.</p> : candidates.map((finding) => {
                const unavailable = unavailableIds.has(finding.id);
                return (
                  <label key={finding.id} className={cn("flex items-start gap-2 rounded-md border border-transparent p-2 text-xs hover:bg-muted", unavailable && "opacity-60")}>
                    <input type="checkbox" checked={selectedIds.has(finding.id)} disabled={(unavailable && !selectedIds.has(finding.id)) || (!selectedIds.has(finding.id) && selectedIds.size >= 200)} onChange={() => toggle(finding.id)} className="mt-0.5 accent-primary" />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2"><span className="font-medium">{finding.title}</span><Badge variant="outline" className={cn("text-[10px]", SEVERITY_CLASS[finding.severity])}>{finding.severity}</Badge>{unavailable ? <span className="text-destructive">unavailable — remove before saving</span> : null}</span>
                      <span className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">{finding.target ?? finding.id}</span>
                    </span>
                  </label>
                );
              })}
            </div>
            <p className="text-xs font-medium">{selectedIds.size} selected</p>
          </div>
        ) : null}

        {step === "review" ? (
          <div className="space-y-4">
            <div className="rounded-md border border-border bg-muted/20 p-4">
              <p className="font-medium">{name}</p>
              <p className="mt-1 text-sm text-muted-foreground">{rationale}</p>
            </div>
            <div>
              <h3 className="text-sm font-medium">{selected.length} member Findings</h3>
              <ul className="mt-2 space-y-1 text-xs">
                {selected.map((finding) => <li key={finding.id} className="flex justify-between gap-3 rounded border border-border p-2"><span>{finding.title}</span><span className="font-mono text-muted-foreground">{finding.id.slice(0, 8)}</span></li>)}
              </ul>
            </div>
            <p className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-900 dark:text-emerald-100">
              Creating or editing this group does not merge, hide, delete, revalidate, or change reportability for any member Finding.
            </p>
          </div>
        ) : null}

        {error ? (
          <div role="alert" className="flex items-center justify-between gap-3 rounded-md border border-destructive/40 p-3 text-sm text-destructive">
            <span>{error}</span>
            {staleConflict ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
                  void qc.refetchQueries({ queryKey: qk.findingGroups(slug) });
                  onClose();
                }}
              >
                Reload latest
              </Button>
            ) : null}
          </div>
        ) : null}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          {step !== "details" ? <Button type="button" variant="outline" onClick={() => setStep(step === "review" ? "members" : "details")} disabled={busy}>Back</Button> : null}
          {step === "details" ? <Button type="button" disabled={!detailsReady} onClick={() => setStep("members")}>Choose Findings</Button> : null}
          {step === "members" ? <Button type="button" disabled={!membersReady} onClick={() => setStep("review")}>Review group</Button> : null}
          {step === "review" ? <Button type="button" disabled={!membersReady || busy} onClick={() => void save()}>{busy ? "Saving…" : group ? "Save group" : "Create group"}</Button> : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function FindingActionWizard({
  slug,
  findings,
  onClose,
  onFindingCreated,
}: {
  slug: string;
  findings: Finding[];
  onClose: () => void;
  onFindingCreated: (finding: Finding) => void;
}) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"choose" | "finding" | "group">("choose");
  if (mode === "finding") {
    return <FindingCreateDialog slug={slug} onClose={onClose} onCreated={async (finding) => { onFindingCreated(finding); await Promise.all([qc.invalidateQueries({ queryKey: qk.findings(slug) }), qc.invalidateQueries({ queryKey: qk.findingHierarchy(slug) })]); onClose(); }} />;
  }
  if (mode === "group") {
    return <FindingGroupDialog slug={slug} findings={findings} group={null} onClose={onClose} onSaved={onClose} />;
  }
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Create or organize Findings</DialogTitle>
          <DialogDescription>Choose an independent custom Finding or a non-destructive presentation group.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <button type="button" onClick={() => setMode("finding")} className="rounded-lg border border-border p-4 text-left hover:bg-muted"><Plus className="mb-3 h-5 w-5" /><span className="font-medium">Custom Finding</span><span className="mt-1 block text-xs text-muted-foreground">Record an issue, observation, or analyst conclusion that tooling missed.</span></button>
          <button type="button" onClick={() => setMode("group")} className="rounded-lg border border-border p-4 text-left hover:bg-muted"><FolderPlus className="mb-3 h-5 w-5" /><span className="font-medium">Finding group</span><span className="mt-1 block text-xs text-muted-foreground">Organize existing Findings without merging or changing their lifecycle.</span></button>
        </div>
        <DialogFooter><Button type="button" variant="outline" onClick={onClose}>Cancel</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function FindingGroupsPanel({
  slug,
  findings,
  canWrite,
}: {
  slug: string;
  findings: Finding[];
  canWrite: boolean;
}) {
  const qc = useQueryClient();
  const query = useFindingGroups(slug);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const groups = query.data ?? [];
  const selected = groups.find((group) => group.id === selectedId) ?? null;
  const editing = groups.find((group) => group.id === editingId) ?? null;
  const deleting = groups.find((group) => group.id === deletingId) ?? null;
  const returnTo = `/e?slug=${encodeURIComponent(slug)}&view=findings`;

  const dissolve = async () => {
    if (!deleting || busy) return;
    setBusy(true);
    setError(null);
    try {
      await deleteFindingGroup(slug, deleting.id, deleting.row_version);
      await qc.invalidateQueries({ queryKey: qk.findingGroups(slug) });
      setDeletingId(null);
      setSelectedId(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setDeletingId(null);
      await query.refetch();
    } finally {
      setBusy(false);
    }
  };

  if (query.isLoading && !query.data) {
    return <p className="text-xs text-muted-foreground">Loading custom groups…</p>;
  }
  if (query.error && !query.data) {
    return <p className="text-xs text-destructive">Custom groups could not be loaded.</p>;
  }
  if (groups.length === 0) return null;
  return (
    <section className="space-y-2" aria-label="Custom Finding groups">
      <div>
        <h3 className="text-sm font-semibold">Custom groups</h3>
        <p className="text-xs text-muted-foreground">
          Analyst-created presentation views. Member lifecycle and evidence remain independent.
        </p>
        {query.error ? (
          <p className="mt-1 text-xs text-destructive">
            Refresh failed; displayed group information may be stale.
          </p>
        ) : null}
      </div>
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        {groups.map((group) => (
          <button
            key={group.id}
            type="button"
            onClick={() => {
              setError(null);
              setSelectedId(group.id);
            }}
            className="rounded-lg border border-border p-3 text-left hover:bg-muted"
          >
            <span className="flex items-start justify-between gap-2">
              <span className="font-medium">{group.name}</span>
              <Badge
                variant="outline"
                className={cn("text-[10px]", SEVERITY_CLASS[group.rollup.max_severity])}
              >
                {group.rollup.max_severity}
              </Badge>
            </span>
            <span className="mt-1 line-clamp-2 block text-xs text-muted-foreground">
              {group.rationale}
            </span>
            <span className="mt-2 block text-[11px] text-muted-foreground">
              {group.rollup.available_members} available member
              {group.rollup.available_members === 1 ? "" : "s"}
              {group.rollup.unavailable_members
                ? ` · ${group.rollup.unavailable_members} unavailable`
                : ""}
            </span>
          </button>
        ))}
      </div>
      {selected ? (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) {
              setSelectedId(null);
              setError(null);
            }
          }}
        >
          <DialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto">
            <DialogHeader>
              <DialogTitle>{selected.name}</DialogTitle>
              <DialogDescription>{selected.rationale}</DialogDescription>
            </DialogHeader>
            <div className="flex gap-2">
              <Badge
                variant="outline"
                className={SEVERITY_CLASS[selected.rollup.max_severity]}
              >
                {selected.rollup.max_severity}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {selected.rollup.member_count} members · version {selected.row_version}
              </span>
            </div>
            <div className="space-y-2">
              {selected.members.map((member) => {
                const content = (
                  <>
                    <span className="font-medium">{member.finding.title}</span>
                    <span className="mt-0.5 block text-xs text-muted-foreground">
                      {member.finding.severity} · {member.finding.status.replaceAll("_", " ")}
                      {member.available ? "" : " · unavailable historical member"}
                    </span>
                  </>
                );
                return member.available ? (
                  <Link
                    key={member.finding_id}
                    href={`/e/findings/${member.finding_id}?slug=${encodeURIComponent(slug)}&returnTo=${encodeURIComponent(returnTo)}`}
                    className="block rounded-md border border-border p-2 text-sm hover:bg-muted"
                  >
                    {content}
                  </Link>
                ) : (
                  <div
                    key={member.finding_id}
                    className="rounded-md border border-dashed border-border p-2 text-sm opacity-60"
                  >
                    {content}
                  </div>
                );
              })}
            </div>
            {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
            <DialogFooter>
              {canWrite ? (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setDeletingId(selected.id)}
                  >
                    <Trash2 className="mr-1.5 h-4 w-4" />
                    Dissolve
                  </Button>
                  <Button
                    type="button"
                    onClick={() => {
                      setEditingId(selected.id);
                      setSelectedId(null);
                    }}
                  >
                    <Pencil className="mr-1.5 h-4 w-4" />
                    Edit
                  </Button>
                </>
              ) : null}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
      {editing ? (
        <FindingGroupDialog
          key={`${editing.id}:${editing.row_version}`}
          slug={slug}
          findings={findings}
          group={editing}
          onClose={() => setEditingId(null)}
          onSaved={(saved) => {
            setSelectedId(saved.id);
            setEditingId(null);
          }}
        />
      ) : null}
      <ConfirmDialog
        open={deleting !== null}
        title="Dissolve this custom group?"
        description="Only the presentation group is removed. Every member Finding, evidence record, validation decision, report state, and history remains unchanged."
        confirmLabel="Dissolve group"
        busyLabel="Dissolving…"
        busy={busy}
        onConfirm={() => void dissolve()}
        onOpenChange={(open) => !open && !busy && setDeletingId(null)}
      />
    </section>
  );
}
