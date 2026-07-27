"use client";

import Link from "next/link";
import { Bell, ChevronRight } from "lucide-react";
import { useRef, useState } from "react";
import { ApprovalsModal, type PendingApproval } from "@/components/approvals-modal";
import { RunDetailModal } from "@/components/playbooks/run-detail-modal";
import { QueryState } from "@/components/query-state";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useDecisionInbox, useMe } from "@/lib/hooks";
import type { ToolDecisionInboxItem } from "@/lib/types";
import { cn } from "@/lib/utils";

function toPending(row: ToolDecisionInboxItem): PendingApproval {
  return {
    approval_id: row.id,
    thread_id: row.thread_id,
    tool: row.tool_name,
    args: row.tool_args,
    risk: row.risk,
    scope: row.scope_check,
    engagement_slug: row.engagement_slug,
    engagement_name: row.engagement_name,
  };
}

function age(value: string): string {
  const elapsed = Math.max(0, Date.now() - new Date(value).getTime());
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

// Tenant-global decision inbox. Both legacy tool approvals and gated v3
// playbook runs appear here; each keeps its existing decision modal and write
// endpoint so their state-machine semantics remain separate.
export function ApprovalInbox({
  variant = "icon",
  collapsed = false,
}: {
  variant?: "icon" | "sidebar";
  collapsed?: boolean;
} = {}) {
  const query = useDecisionInbox();
  const { data: me } = useMe();
  const canWrite = me !== undefined && me.role !== "guest";
  const decisions = query.data ?? [];
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [selectedApproval, setSelectedApproval] = useState<PendingApproval | null>(null);
  const [selectedPlaybookRunId, setSelectedPlaybookRunId] = useState<string | null>(null);

  const isSidebar = variant === "sidebar";
  const badgeCount = decisions.length;
  const label = `${badgeCount} pending decision${badgeCount === 1 ? "" : "s"}`;

  const button = isSidebar ? (
    <button
      ref={triggerRef}
      type="button"
      aria-label={`Notifications — ${label}`}
      title={collapsed ? `Notifications (${badgeCount})` : undefined}
      className={cn(
        "flex w-full items-center gap-3 rounded-md text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
        collapsed ? "justify-center px-0 py-2" : "px-3 py-2",
      )}
    >
      <span className="relative shrink-0">
        <Bell className="h-4 w-4" />
        {badgeCount > 0 && (
          <span className="absolute -right-1.5 -top-1.5 min-w-4 rounded-full bg-amber-500 px-1 text-center text-[9px] font-semibold leading-4 text-black">
            {badgeCount > 99 ? "99+" : badgeCount}
          </span>
        )}
      </span>
      {!collapsed && <span className="flex-1 text-left">Notifications</span>}
    </button>
  ) : (
    <button
      ref={triggerRef}
      type="button"
      aria-label={label}
      className="relative rounded border border-border p-1.5 text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
    >
      <Bell className="h-4 w-4" />
      {badgeCount > 0 && (
        <span className="absolute -right-1.5 -top-1.5 min-w-4 rounded-full bg-amber-500 px-1 text-center text-[9px] font-semibold leading-4 text-black">
          {badgeCount > 99 ? "99+" : badgeCount}
        </span>
      )}
    </button>
  );

  return (
    <div className={isSidebar ? "w-full" : "relative"}>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger asChild>{button}</DialogTrigger>
        <DialogContent className="max-h-[80vh] max-w-lg gap-0 overflow-hidden p-0">
          <DialogHeader className="border-b border-border px-4 py-3 pr-12">
            <DialogTitle className="text-sm">Pending decisions</DialogTitle>
            <DialogDescription className="text-[10px]">
              Closing this inbox never approves or denies an action.
            </DialogDescription>
          </DialogHeader>

          <div className="max-h-[28rem] overflow-y-auto">
            {query.data === undefined && (query.isLoading || query.error) ? (
              <div className="p-4">
                <QueryState
                  isLoading={query.isLoading}
                  error={query.error}
                  loadingLabel="Loading pending decisions…"
                  errorLabel="Could not load pending decisions."
                  onRetry={() => void query.refetch()}
                  isRetrying={query.isFetching}
                />
              </div>
            ) : (
              <>
                {query.error && (
                  <div className="p-3 pb-0">
                    <QueryState
                      isLoading={false}
                      error={query.error}
                      hasData
                      compact
                      onRetry={() => void query.refetch()}
                      isRetrying={query.isFetching}
                    />
                  </div>
                )}
                {decisions.length === 0 ? (
                  <p className="p-4 text-sm text-muted-foreground">
                    Nothing is waiting for a decision.
                  </p>
                ) : (
                  <ul className="divide-y divide-border">
                    {decisions.map((row) => (
                      <li key={`${row.kind}-${row.id}`}>
                        <button
                          type="button"
                          disabled={row.kind === "tool_approval" && !canWrite}
                          title={
                            row.kind === "tool_approval" && !canWrite
                              ? "Guest access is read-only"
                              : undefined
                          }
                          onClick={() => {
                            if (row.kind === "tool_approval" && !canWrite) return;
                            setOpen(false);
                            if (row.kind === "tool_approval") {
                              setSelectedApproval(toPending(row));
                            } else {
                              setSelectedPlaybookRunId(row.id);
                            }
                          }}
                          className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-muted/40 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          <span className="mt-0.5 rounded-full border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 text-[10px] uppercase text-amber-700 dark:text-amber-200">
                            {row.kind === "tool_approval" ? row.risk : "Playbook"}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium">
                              {row.kind === "tool_approval" ? row.tool_name : row.playbook_name}
                            </span>
                            <span className="block truncate text-xs text-muted-foreground">
                              {row.engagement_name} · {row.engagement_slug}
                            </span>
                            <span className="mt-1 block font-mono text-[10px] text-muted-foreground">
                              {row.kind === "tool_approval"
                                ? `${row.thread_id.slice(0, 12)}…`
                                : `${row.scope_subset.length} target${row.scope_subset.length === 1 ? "" : "s"}`}
                              {" · "}{age(row.created_at)}
                            </span>
                          </span>
                          <ChevronRight className="mt-2 h-4 w-4 shrink-0 text-muted-foreground" />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>

          {decisions.length > 0 && (
            <div className="border-t border-border px-4 py-2 text-right">
              <Link
                href={`/e?slug=${encodeURIComponent(decisions[0].engagement_slug)}&view=status`}
                onClick={() => setOpen(false)}
                className="text-xs text-muted-foreground hover:text-foreground hover:underline"
              >
                Open oldest engagement Runs
              </Link>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <ApprovalsModal
        pending={selectedApproval}
        onResolved={() => setSelectedApproval(null)}
        onClose={() => setSelectedApproval(null)}
      />
      {selectedPlaybookRunId && (
        <RunDetailModal
          runId={selectedPlaybookRunId}
          onClose={() => setSelectedPlaybookRunId(null)}
          returnFocus={() => triggerRef.current?.focus()}
          canWrite={canWrite}
        />
      )}
    </div>
  );
}
