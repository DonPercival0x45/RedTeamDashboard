"use client";

// v1.10.0: Live run side panel.
//
// A right-side slide-over that streams a single agent run's actions in
// real time via the engagement SSE feed (filtered by thread_id). This is
// where the kickoff toast's "Open ->" lands, so starting a run and
// watching it no longer requires hopping to the Status tab and finding
// the row.
//
// Reuses summarizeEvent / EVENT_COLORS from status-view so event rows
// look identical to the Status "Live events" tail.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Sparkles, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { subscribeToEvents } from "@/lib/events";
import {
  EVENT_COLORS,
  summarizeEvent,
} from "@/components/status-view";
import { useRunToast } from "@/components/run-toast-provider";
import type { RunEvent } from "@/lib/types";

export interface RunPanelRef {
  slug: string;
  threadId: string;
  runSlug: string;
  label: string;
  sublabel?: string;
}

interface LoggedEvent {
  sseId: string;
  event: RunEvent;
  at: number;
}

export function RunPanel({
  run,
  onClose,
}: {
  run: RunPanelRef;
  onClose: () => void;
}) {
  const [events, setEvents] = useState<LoggedEvent[]>([]);
  const [status, setStatus] = useState<
    "connecting" | "live" | "ended" | "error"
  >("connecting");
  const scrollRef = useRef<HTMLUListElement | null>(null);
  const { fire: fireToast } = useRunToast();
  const completionToastFired = useRef(false);

  // v0.19.1 (roadmap #23): count findings this run produced so we can
  // surface 'N new findings added' when it ends — the run->findings
  // step used to be silent.
  const findingsCount = useMemo(
    () => events.filter((e) => e.event.type === "finding.created").length,
    [events],
  );
  const ended = status === "ended";

  // Reset the one-shot toast guard when the watched run changes.
  useEffect(() => {
    completionToastFired.current = false;
  }, [run.threadId]);

  // Fire the 'N new findings' toast once when the run reaches a terminal
  // state and actually produced findings. Best-effort — the inline banner
  // below is the primary surface; the toast covers the case where the
  // analyst navigated away from the panel.
  useEffect(() => {
    if (!ended || completionToastFired.current) return;
    completionToastFired.current = true;
    if (findingsCount > 0) {
      fireToast({
        kind: "agent",
        runSlug: run.runSlug,
        label: `✓ ${findingsCount} new finding${
          findingsCount === 1 ? "" : "s"
        }`,
        sublabel: run.label,
        openHref: `/e/${run.slug}`,
      });
    }
  }, [ended, findingsCount, fireToast, run.runSlug, run.slug, run.label]);

  // Keep the latest events for this run keyed by sseId so reconnects /
  // replays (Last-Event-ID) don't duplicate rows.
  const onEvent = useCallback((event: RunEvent, sseId: string | undefined) => {
    const id = sseId || `${event.type}-${Math.random().toString(36).slice(2, 8)}`;
    setEvents((prev) => {
      if (prev.some((e) => e.sseId === id)) return prev;
      return [...prev, { sseId: id, event, at: Date.now() }];
    });
    if (event.type === "run.completed" || event.type === "run.errored") {
      setStatus("ended");
    } else {
      setStatus("live");
    }
  }, []);

  useEffect(() => {
    setEvents([]);
    setStatus("connecting");
    const controller = new AbortController();
    let lastId: string | undefined;
    subscribeToEvents({
      slug: run.slug,
      thread: run.threadId,
      signal: controller.signal,
      lastEventId: lastId,
      onOpen: () => setStatus((s) => (s === "connecting" ? "live" : s)),
      onEvent: (ev, sseId) => {
        if (sseId) lastId = sseId;
        onEvent(ev, sseId);
      },
      onError: () => setStatus("error"),
    }).catch(() => setStatus("error"));
    return () => controller.abort();
    // Re-subscribe if the run target changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.slug, run.threadId]);

  // Auto-scroll to the newest event as they arrive (only if the user is
  // already near the bottom, so we don't yank them while scrolling up).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [events]);

  // Escape to close.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const dot =
    status === "live"
      ? "bg-emerald-500"
      : status === "connecting"
        ? "bg-amber-500"
        : status === "ended"
          ? "bg-zinc-500"
          : "bg-rose-500";
  const statusLabel =
    status === "live"
      ? "Live"
      : status === "connecting"
        ? "Connecting…"
        : status === "ended"
          ? "Run ended"
          : "Disconnected";

  return (
    <>
      <div
        className="fixed inset-0 z-[90] bg-black/60"
        onClick={onClose}
        aria-hidden
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Run ${run.runSlug} live view`}
        className="fixed right-0 top-0 z-[100] flex h-full w-[min(560px,96vw)] flex-col border-l border-border bg-popover shadow-2xl"
      >
        <header className="flex items-start justify-between gap-3 border-b border-border p-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="rounded border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 font-mono text-[10px] text-emerald-200">
                {run.runSlug}
              </span>
              <span className="text-sm font-medium text-foreground">
                {run.label}
              </span>
            </div>
            {run.sublabel && (
              <p className="mt-1 truncate text-[11px] text-muted-foreground">
                {run.sublabel}
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <span
                className={cn(
                  "inline-block h-2 w-2 rounded-full",
                  dot,
                  status === "live" && "animate-pulse",
                )}
              />
              {statusLabel}
            </span>
            <button
              type="button"
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </header>

        <ul
          ref={scrollRef}
          className="flex-1 space-y-1.5 overflow-y-auto p-3 font-mono text-xs"
        >
          {events.length === 0 && status === "connecting" && (
            <li className="flex items-center gap-2 px-1 py-2 text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Waiting for the worker to emit events…
            </li>
          )}
          {events.length === 0 && status !== "connecting" && (
            <li className="px-1 py-2 text-muted-foreground">
              No events yet for this run.
            </li>
          )}
          {events.map((entry) => (
            <li
              key={entry.sseId}
              className="flex items-start gap-2 rounded border-l-2 border-border bg-secondary/30 px-2 py-1.5"
            >
              <Badge
                variant="outline"
                className={cn(
                  "shrink-0 text-[10px]",
                  EVENT_COLORS[entry.event.type] ?? "",
                )}
              >
                {entry.event.type}
              </Badge>
              <span className="break-all text-muted-foreground">
                {summarizeEvent(entry.event)}
              </span>
            </li>
          ))}
        </ul>

        {ended && (
          <div
            className={cn(
              "flex items-center gap-2 border-t px-4 py-2.5 text-xs",
              findingsCount > 0
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                : "border-border bg-secondary/30 text-muted-foreground",
            )}
          >
            {findingsCount > 0 ? (
              <>
                <Sparkles className="h-3.5 w-3.5" />
                <span className="font-medium">
                  {findingsCount} new finding{findingsCount === 1 ? "" : "s"}{" "}
                  added
                </span>
                <span className="text-muted-foreground">
                  — review them on the Findings tab.
                </span>
              </>
            ) : (
              <span>Run complete — no new findings.</span>
            )}
          </div>
        )}

        <footer className="border-t border-border px-4 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">
          SSE · thread {run.threadId.slice(0, 8)}… · engagement {run.slug}
        </footer>
      </aside>
    </>
  );
}
