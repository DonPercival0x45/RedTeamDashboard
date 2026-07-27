"use client";

// v3 Track A — analyst-facing Playbooks tab on the engagement Automation page.
//
// Two sections stacked:
//   1. Catalog — cards showing every playbook; "Kick run" button per card.
//   2. Runs — table of runs for this engagement (newest first) with status
//      pills + action affordances (approve/reject/cancel/view). Polls every
//      3s while anything is running/pending/awaiting; 15s otherwise.

import { useEffect, useMemo, useState } from "react";
import { Pencil, Play, Plus, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { KickRunModal } from "@/components/playbooks/kick-run-modal";
import { PlaybookEditorModal } from "@/components/playbooks/playbook-editor-modal";
import { RunDetailModal } from "@/components/playbooks/run-detail-modal";
import { QueryState } from "@/components/query-state";
import { useMe, usePlaybooks, usePlaybookRuns } from "@/lib/hooks";
import {
  isPlaybookApplicable,
  PLAYBOOK_CATEGORY_LABEL,
  PLAYBOOK_CATEGORY_ORDER,
  sortPlaybooks,
  type PlaybookSort,
} from "@/lib/playbook-catalog";
import { cn } from "@/lib/utils";
import type {
  PlaybookCategory,
  PlaybookRead,
  PlaybookRunRead,
  PlaybookRunStatus,
} from "@/lib/types";

const STATUS_BADGE: Record<PlaybookRunStatus, string> = {
  awaiting_approval: "border-amber-500/40 text-amber-700 dark:text-amber-300",
  pending: "border-sky-500/40 text-sky-700 dark:text-sky-300",
  running: "border-blue-500/40 text-blue-700 dark:text-blue-300",
  completed: "border-emerald-500/40 text-emerald-700 dark:text-emerald-300",
  partial: "border-amber-500/40 text-amber-700 dark:text-amber-300",
  failed: "border-rose-500/40 text-rose-700 dark:text-rose-300",
  cancelled: "border-zinc-500/40 text-muted-foreground",
};

const STATUS_LABEL: Record<PlaybookRunStatus, string> = {
  awaiting_approval: "Awaiting approval",
  pending: "Pending",
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  failed: "Failed",
  cancelled: "Cancelled",
};

function StatusBadge({ status }: { status: PlaybookRunStatus }) {
  return (
    <Badge variant="outline" className={cn("text-xs", STATUS_BADGE[status])}>
      {STATUS_LABEL[status]}
    </Badge>
  );
}

function PlaybookCard({
  playbook,
  onKick,
  onEdit,
  canRun,
}: {
  playbook: PlaybookRead;
  onKick: (pb: PlaybookRead) => void;
  onEdit?: (pb: PlaybookRead) => void;
  canRun: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h4 className="font-medium text-sm">{playbook.name}</h4>
            <span className="text-xs text-muted-foreground">
              v{playbook.version}
            </span>
            {playbook.origin === "custom" ? (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                Custom
              </Badge>
            ) : null}
            {playbook.active ? (
              <Badge
                variant="outline"
                className="border-amber-500/40 text-amber-700 dark:text-amber-300 text-[10px] px-1.5 py-0"
              >
                Gated
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 text-xs text-muted-foreground line-clamp-2">
            {playbook.description || "No description."}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {playbook.step_count} steps · {PLAYBOOK_CATEGORY_LABEL[playbook.category ?? "other"]} ·{" "}
          {(playbook.applicable_entity_types?.length
            ? playbook.applicable_entity_types
            : [playbook.applies_to_asset_class]
          ).join(", ")}
        </span>
        <div className="flex items-center gap-1">
          {onEdit ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-label={`Edit ${playbook.name}`}
              onClick={() => onEdit(playbook)}
            >
              <Pencil className="mr-1 h-3 w-3" /> Edit
            </Button>
          ) : null}
          {canRun ? (
            <Button
              size="sm"
              aria-label={`Run ${playbook.name}`}
              disabled={playbook.step_count === 0}
              onClick={() => onKick(playbook)}
            >
              <Play className="mr-1 h-3 w-3" />
              Run
            </Button>
          ) : (
            <span title="Guest accounts can review recipes but cannot start runs">
              Read-only
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function RunRow({
  run,
  onOpen,
}: {
  run: PlaybookRunRead;
  onOpen: (r: PlaybookRunRead) => void;
}) {
  const scope = Array.isArray(run.scope_subset)
    ? run.scope_subset.map((s) => String(s)).join(", ")
    : "";
  const started = run.started_at
    ? new Date(run.started_at).toLocaleString()
    : "—";
  return (
    <tr
      className="hover:bg-muted/40 cursor-pointer"
      onClick={() => onOpen(run)}
    >
      <td className="px-3 py-2">
        <StatusBadge status={run.status} />
      </td>
      <td className="px-3 py-2 text-sm">
        {run.playbook_slug}{" "}
        <span className="text-xs text-muted-foreground">
          v{run.playbook_version}
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground uppercase">
        {run.executor}
      </td>
      <td className="px-3 py-2 text-xs">
        <span className="line-clamp-1 max-w-[16rem]">{scope || "—"}</span>
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">
        {run.steps_succeeded}/{run.steps_total}
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">{started}</td>
      <td className="px-3 py-2 text-right">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-label={`Manage ${run.playbook_slug} run`}
          onClick={(event) => {
            event.stopPropagation();
            onOpen(run);
          }}
        >
          Manage
        </Button>
      </td>
    </tr>
  );
}

export function PlaybooksTab({
  engagementSlug,
  initialTarget,
  onTargetConsumed,
  showCreateAction = true,
}: {
  engagementSlug: string;
  initialTarget?: { type: string; value: string } | null;
  onTargetConsumed?: () => void;
  showCreateAction?: boolean;
}) {
  const playbooksQuery = usePlaybooks();
  const runsQuery = usePlaybookRuns(engagementSlug);
  const meQuery = useMe();
  const canWrite = meQuery.data !== undefined && meQuery.data.role !== "guest";
  const [kickPlaybook, setKickPlaybook] = useState<PlaybookRead | null>(null);
  const [editPlaybook, setEditPlaybook] = useState<PlaybookRead | null | undefined>(
    undefined,
  );
  const [openRun, setOpenRun] = useState<PlaybookRunRead | null>(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<"all" | PlaybookCategory>("all");
  const targetType = initialTarget?.type ?? null;
  const targetValue = initialTarget?.value ?? null;
  const hasTarget = targetType !== null && targetValue !== null;
  const [sort, setSort] = useState<PlaybookSort>(
    hasTarget ? "recommended" : "name",
  );
  const [showIncompatible, setShowIncompatible] = useState(!hasTarget);

  useEffect(() => {
    setCategory("all");
    setSort(hasTarget ? "recommended" : "name");
    setShowIncompatible(!hasTarget);
  }, [hasTarget, targetType, targetValue]);

  const catalog = useMemo(() => playbooksQuery.data ?? [], [playbooksQuery.data]);
  const targetFiltered = useMemo(
    () =>
      initialTarget && !showIncompatible
        ? catalog.filter((playbook) =>
            isPlaybookApplicable(playbook, initialTarget.type),
          )
        : catalog,
    [catalog, initialTarget, showIncompatible],
  );
  const query = search.trim().toLowerCase();
  const searched = useMemo(
    () =>
      targetFiltered.filter(
        (playbook) =>
          !query ||
          playbook.name.toLowerCase().includes(query) ||
          playbook.slug.toLowerCase().includes(query) ||
          (playbook.description ?? "").toLowerCase().includes(query),
      ),
    [query, targetFiltered],
  );
  const categoryCounts = useMemo(
    () =>
      new Map(
        PLAYBOOK_CATEGORY_ORDER.map((value) => [
          value,
          searched.filter((playbook) => (playbook.category ?? "other") === value)
            .length,
        ]),
      ),
    [searched],
  );
  const visibleCatalog = useMemo(
    () =>
      sortPlaybooks(
        searched.filter(
          (playbook) =>
            category === "all" || (playbook.category ?? "other") === category,
        ),
        sort,
        initialTarget?.type,
      ),
    [category, initialTarget?.type, searched, sort],
  );
  const runs = runsQuery.data ?? [];
  const awaiting = runs.filter((r) => r.status === "awaiting_approval");

  return (
    <div className="space-y-6">
      {awaiting.length > 0 ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
          <span className="font-medium">
            {awaiting.length} run{awaiting.length === 1 ? "" : "s"} awaiting
            approval
          </span>
          <span className="ml-2 text-xs text-muted-foreground">
            Click a row below to review.
          </span>
        </div>
      ) : null}

      <section>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold">Playbook catalog</h3>
            <p className="text-xs text-muted-foreground">
              Browse by purpose, target compatibility, and ordered execution steps.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {initialTarget ? (
              <p className="rounded-md border border-primary/30 bg-primary/5 px-2.5 py-1 text-xs">
                Target context · {initialTarget.type}:{" "}
                <span className="font-mono">{initialTarget.value}</span>
              </p>
            ) : null}
            {canWrite && showCreateAction ? (
              <Button type="button" size="sm" onClick={() => setEditPlaybook(null)}>
                <Plus className="mr-1 h-3.5 w-3.5" /> New playbook
              </Button>
            ) : null}
          </div>
        </div>
        {playbooksQuery.data === undefined &&
        (playbooksQuery.isLoading || playbooksQuery.error) ? (
          <QueryState
            isLoading={playbooksQuery.isLoading}
            error={playbooksQuery.error}
            loadingLabel="Loading playbooks…"
            errorLabel="Could not load the playbook catalog."
            onRetry={() => void playbooksQuery.refetch()}
            isRetrying={playbooksQuery.isFetching}
          />
        ) : (
          <>
            <QueryState
              isLoading={false}
              error={playbooksQuery.error}
              hasData
              compact
              onRetry={() => void playbooksQuery.refetch()}
              isRetrying={playbooksQuery.isFetching}
            />
            {catalog.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No playbooks in the catalog yet.
              </p>
            ) : (
              <div className="space-y-3">
                <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_12rem_auto]">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      aria-label="Search playbooks"
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      placeholder="Search playbooks…"
                      className="pl-9"
                    />
                  </div>
                  <select
                    aria-label="Sort playbooks"
                    value={sort}
                    onChange={(event) => setSort(event.target.value as PlaybookSort)}
                    className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                  >
                    {initialTarget ? <option value="recommended">Best match</option> : null}
                    <option value="name">Name A–Z</option>
                    <option value="steps_desc">Most steps</option>
                    <option value="steps_asc">Fewest steps</option>
                  </select>
                  {initialTarget ? (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setShowIncompatible((current) => !current)}
                    >
                      {showIncompatible ? "Applicable only" : "Show all playbooks"}
                    </Button>
                  ) : null}
                </div>
                <Tabs value={category} onValueChange={(value) => setCategory(value as "all" | PlaybookCategory)}>
                  <TabsList>
                    <TabsTrigger value="all">All ({searched.length})</TabsTrigger>
                    {PLAYBOOK_CATEGORY_ORDER.map((value) => (
                      <TabsTrigger key={value} value={value}>
                        {PLAYBOOK_CATEGORY_LABEL[value]} ({categoryCounts.get(value) ?? 0})
                      </TabsTrigger>
                    ))}
                  </TabsList>
                  <TabsContent value={category} className="pt-3">
                    {visibleCatalog.length === 0 ? (
                      <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
                        No playbooks match this category, target, and search.
                      </p>
                    ) : (
                      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-live="polite">
                        {visibleCatalog.map((playbook) => (
                          <PlaybookCard
                            key={playbook.id}
                            playbook={playbook}
                            onKick={setKickPlaybook}
                            canRun={canWrite}
                            onEdit={
                              canWrite && playbook.can_edit !== false
                                ? setEditPlaybook
                                : undefined
                            }
                          />
                        ))}
                      </div>
                    )}
                  </TabsContent>
                </Tabs>
              </div>
            )}
          </>
        )}
      </section>

      <section>
        <h3 className="text-sm font-semibold mb-3">Runs</h3>
        {runsQuery.data === undefined && (runsQuery.isLoading || runsQuery.error) ? (
          <QueryState
            isLoading={runsQuery.isLoading}
            error={runsQuery.error}
            loadingLabel="Loading runs…"
            errorLabel="Could not load playbook runs."
            onRetry={() => void runsQuery.refetch()}
            isRetrying={runsQuery.isFetching}
          />
        ) : (
          <>
            <QueryState
              isLoading={false}
              error={runsQuery.error}
              hasData
              compact
              onRetry={() => void runsQuery.refetch()}
              isRetrying={runsQuery.isFetching}
            />
            {runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No playbook runs on this engagement yet.
              </p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-left">
                  <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Status</th>
                      <th className="px-3 py-2">Playbook</th>
                      <th className="px-3 py-2">Executor</th>
                      <th className="px-3 py-2">Scope</th>
                      <th className="px-3 py-2">Steps</th>
                      <th className="px-3 py-2">Started</th>
                      <th className="px-3 py-2 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {runs.map((run) => (
                      <RunRow key={run.id} run={run} onOpen={setOpenRun} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      {kickPlaybook ? (
        <KickRunModal
          engagementSlug={engagementSlug}
          playbook={kickPlaybook}
          initialTarget={
            initialTarget && isPlaybookApplicable(kickPlaybook, initialTarget.type)
              ? initialTarget
              : null
          }
          onStarted={onTargetConsumed}
          onClose={() => setKickPlaybook(null)}
        />
      ) : null}
      {editPlaybook !== undefined ? (
        <PlaybookEditorModal
          playbook={editPlaybook}
          onClose={() => setEditPlaybook(undefined)}
        />
      ) : null}
      {openRun ? (
        <RunDetailModal
          runId={openRun.id}
          canWrite={canWrite}
          onClose={() => setOpenRun(null)}
        />
      ) : null}
    </div>
  );
}
