"use client";

// v1.2.0: Cross-portal run tracking.
//
// Anywhere in the app kicks off an agent run — the engagement /runs
// prompt, the Feedback page's "AI Feedback" re-evaluate button, the
// Prioritize (agent) + Combine (agent) buttons, Triage from the
// finding slide-over — this provider fires a small toast bottom-right
// with the run's ``rt-XXXX`` slug and an "Open →" deep link.
//
// Deep-link targets:
//   - engagement-scoped runs (worker `run.started` events on the
//     Redis outbound stream) → ``/e/<slug>?run=<threadOrExecId>``
//   - tenant-global runs (planner, roadmap re-eval, roadmap combine /
//     rank) → ``/settings/agent-runs?run=<executionId>``
//
// Producers reach the provider via ``useRunToast()`` and call
// ``fire(...)`` on mutation success. The provider owns the toast
// stack + auto-dismiss timers.

import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { ExternalLink, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { RunPanel, type RunPanelRef } from "@/components/run-panel";

interface RunToast {
  id: string;
  runSlug: string;
  label: string;
  openHref: string;
  // Optional detail line under the label (e.g. the prompt snippet or
  // "Prioritize suggestions").
  sublabel?: string;
  // Kind is metadata only — the UI treats them uniformly right now
  // but it's here so a future filter/tray can group by kind.
  kind: "agent" | "planner" | "triage";
  // v1.10.0: when slug + threadId are present the "Open ->" button
  // opens the live side panel instead of navigating. Tenant-global
  // runs (no engagement slug) keep the link behaviour.
  slug?: string;
  threadId?: string;
}

interface RunToastContextValue {
  fire: (t: Omit<RunToast, "id">) => void;
  openRunPanel: (ref: RunPanelRef) => void;
}

const RunToastContext = createContext<RunToastContextValue | null>(null);

const AUTO_DISMISS_MS = 8_000;

export function RunToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<RunToast[]>([]);
  const [activeRun, setActiveRun] = useState<RunPanelRef | null>(null);

  const fire = useCallback((t: Omit<RunToast, "id">) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setToasts((prev) => [...prev, { ...t, id }]);
  }, []);

  const openRunPanel = useCallback((ref: RunPanelRef) => {
    setActiveRun(ref);
  }, []);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <RunToastContext.Provider value={{ fire, openRunPanel }}>
      {children}
      {activeRun && (
        <RunPanel run={activeRun} onClose={() => setActiveRun(null)} />
      )}
      <div
        aria-live="polite"
        aria-label="Run notifications"
        className="pointer-events-none fixed bottom-4 right-4 z-[80] flex w-[min(360px,92vw)] flex-col gap-2"
      >
        {toasts.map((t) => (
          <RunToastCard
            key={t.id}
            toast={t}
            onDismiss={() => dismiss(t.id)}
            onOpenPanel={openRunPanel}
          />
        ))}
      </div>
    </RunToastContext.Provider>
  );
}

function RunToastCard({
  toast,
  onDismiss,
  onOpenPanel,
}: {
  toast: RunToast;
  onDismiss: () => void;
  onOpenPanel: (ref: RunPanelRef) => void;
}) {
  useEffect(() => {
    const timer = window.setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [onDismiss]);

  // v1.10.0: pre-narrow so the panel ref is fully typed (no `!`). When
  // the toast carries an engagement slug + thread id we open the live
  // side panel; otherwise (tenant-global runs) we fall back to the link.
  const panelRef =
    toast.slug && toast.threadId
      ? {
          slug: toast.slug,
          threadId: toast.threadId,
          runSlug: toast.runSlug,
          label: toast.label,
          sublabel: toast.sublabel,
        }
      : null;

  return (
    <div
      role="status"
      className={cn(
        "pointer-events-auto rounded-lg border border-emerald-500/40 bg-popover p-3 shadow-lg",
        "backdrop-blur",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="rounded border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 font-mono text-[10px] text-emerald-700 dark:text-emerald-200">
              {toast.runSlug}
            </span>
            <span className="text-xs font-medium text-foreground">
              {toast.label}
            </span>
          </div>
          {toast.sublabel && (
            <p className="mt-1 truncate text-[11px] text-muted-foreground">
              {toast.sublabel}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 text-muted-foreground hover:text-foreground"
          aria-label="Dismiss"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="mt-2 flex justify-end">
        {panelRef ? (
          <button
            type="button"
            onClick={() => {
              onOpenPanel(panelRef);
              onDismiss();
            }}
            className="inline-flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-foreground transition-colors hover:bg-emerald-500/20"
          >
            Open live <ExternalLink className="h-3 w-3" />
          </button>
        ) : (
          <Link
            href={toast.openHref}
            onClick={onDismiss}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-foreground hover:text-foreground"
          >
            Open <ExternalLink className="h-3 w-3" />
          </Link>
        )}
      </div>
    </div>
  );
}

export function useRunToast(): RunToastContextValue {
  const ctx = useContext(RunToastContext);
  if (!ctx) {
    // Optional consumer — if the provider isn't mounted (e.g. test
    // renderer), swallow the call. This keeps producers dependency-
    // free and lets the toast be a pure UX enhancement.
    return { fire: () => {}, openRunPanel: () => {} };
  }
  return ctx;
}

// Small helper: build the ``rt-XXXX`` slug from a thread_id string or
// UUID. Keep in sync with backend ``app/api/status._run_slug`` and
// ``app/worker/runner`` — same 4-hex-prefix rule.
export function runSlugFromId(source: string): string {
  const hex = source.replace(/-/g, "").toLowerCase();
  return `rt-${hex.slice(0, 4)}`;
}
