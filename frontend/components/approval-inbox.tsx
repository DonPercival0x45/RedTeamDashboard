"use client";

import Link from "next/link";
import { Bell, ChevronRight, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApprovalsModal, type PendingApproval } from "@/components/approvals-modal";
import { usePendingApprovals } from "@/lib/hooks";
import type { ApprovalInboxItem } from "@/lib/types";

function toPending(row: ApprovalInboxItem): PendingApproval {
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

export function ApprovalInbox() {
  const { data, error, isLoading } = usePendingApprovals();
  const approvals = data ?? [];
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<PendingApproval | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !selected) setOpen(false);
    };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("keydown", closeEscape);
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("keydown", closeEscape);
    };
  }, [open, selected]);

  return (
    <div ref={wrapperRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-label={`${approvals.length} pending approvals`}
        aria-expanded={open}
        className="relative rounded border border-border p-1.5 text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
      >
        <Bell className="h-4 w-4" />
        {approvals.length > 0 && (
          <span className="absolute -right-1.5 -top-1.5 min-w-4 rounded-full bg-amber-500 px-1 text-center text-[9px] font-semibold leading-4 text-black">
            {approvals.length > 99 ? "99+" : approvals.length}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-10 z-50 w-[min(28rem,calc(100vw-2rem))] overflow-hidden rounded-lg border border-border bg-popover shadow-2xl">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold">Pending approvals</h2>
              <p className="text-[10px] text-muted-foreground">
                Closing this inbox never approves or denies an action.
              </p>
            </div>
            <button type="button" onClick={() => setOpen(false)} aria-label="Close approval inbox" className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="max-h-[28rem] overflow-y-auto">
            {isLoading ? (
              <p className="p-4 text-sm text-muted-foreground">Loading…</p>
            ) : error ? (
              <p className="p-4 text-sm text-destructive">Could not load approvals.</p>
            ) : approvals.length === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">Nothing is waiting for a decision.</p>
            ) : (
              <ul className="divide-y divide-border">
                {approvals.map((row) => (
                  <li key={row.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(toPending(row))}
                      className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-muted/40"
                    >
                      <span className="mt-0.5 rounded-full border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 text-[10px] uppercase text-amber-700 dark:text-amber-200">
                        {row.risk}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{row.tool_name}</span>
                        <span className="block truncate text-xs text-muted-foreground">{row.engagement_name} · {row.engagement_slug}</span>
                        <span className="mt-1 block font-mono text-[10px] text-muted-foreground">{row.thread_id.slice(0, 12)}… · {age(row.created_at)}</span>
                      </span>
                      <ChevronRight className="mt-2 h-4 w-4 shrink-0 text-muted-foreground" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {approvals.length > 0 && (
            <div className="border-t border-border px-4 py-2 text-right">
              <Link href={`/e?slug=${encodeURIComponent(approvals[0].engagement_slug)}&view=status`} onClick={() => setOpen(false)} className="text-xs text-muted-foreground hover:text-foreground hover:underline">
                Open oldest engagement Status
              </Link>
            </div>
          )}
        </div>
      )}

      <ApprovalsModal
        pending={selected}
        onResolved={() => setSelected(null)}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}
