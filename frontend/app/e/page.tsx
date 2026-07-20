"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  ApprovalsModal,
  type PendingApproval,
} from "@/components/approvals-modal";
import type { LoggedEvent } from "@/lib/types";
import {
  EngagementNav,
  type EngagementView,
} from "@/components/engagement-nav";
import { DossierView } from "@/components/dossier-view";
import { EntitiesView } from "@/components/entities-view";
import { FindingsView } from "@/components/findings-view";
import { ObservationsView } from "@/components/observations-view";
import { CostsView } from "@/components/costs-view";
import { ContributionsView } from "@/components/contributions-view";
import { StatusView } from "@/components/status-view";
import { DiagnosticsView } from "@/components/diagnostics-view";
import { StrategyView } from "@/components/strategy-view";
import { GrantsCard } from "@/components/grants-card";
import { RunPrompt } from "@/components/run-prompt";
import { RunPromptBridgeProvider } from "@/components/run-prompt-context";
import { ScopeEditor } from "@/components/scope-editor";
import { ToolsPanel } from "@/components/tools-panel";
import { subscribeToEvents } from "@/lib/events";
import {
  prefetchEngagementView,
  qk,
  removeFindingFromCache,
  upsertFindingInCache,
  useArchiveEngagementMutation,
  useEngagement,
  useFindings,
  useFlushEngagementMutation,
  useMe,
  useUpdateEngagementMutation,
} from "@/lib/hooks";
import type { Engagement, Finding } from "@/lib/types";

// Slug + active view ride in the query string (?slug=&view=) so the page can be
// statically exported for Azure SWA (no dynamic route segments). The engagement
// opens on Findings — the work product is front and center (see CHARTER).

function formatTimeFrame(eng: Engagement): string {
  switch (eng.time_frame) {
    case "repeatable":
      return "repeatable";
    case "point_in_time_continuous":
      return "point-in-time, continuous";
    case "point_in_time":
      return "point-in-time";
    case "custom":
      return eng.start_date && eng.end_date
        ? `custom ${eng.start_date} → ${eng.end_date}`
        : "custom";
  }
}


const VALID_VIEWS = new Set<EngagementView>([
  "findings",
  "strategy",
  "entities",
  "dossier",
  "observations",
  "costs",
  "scope",
  "status",
  "contributions",
  "diagnostics",
]);

function EngagementDetail({ slug }: { slug: string }) {
  const router = useRouter();
  const params = useSearchParams();
  // Single-tenant: any signed-in analyst can act on the engagement.
  const canWrite = true;

  const viewParam = params.get("view");
  // v2.4.0: naked-URL default landing view is Strategy (was Findings).
  // New engagements skip this — /new redirects straight to
  // /e?slug=X&view=scope after create so the analyst finishes setup
  // before hitting the shared workspace.
  const view: EngagementView =
    viewParam && VALID_VIEWS.has(viewParam as EngagementView)
      ? (viewParam as EngagementView)
      : "strategy";
  const setView = useCallback(
    (next: EngagementView) => {
      const p = new URLSearchParams(params.toString());
      p.set("view", next);
      // View-specific deep-link params (like Strategy's ?workItem=) are
      // consumed once on their view's mount; leaving them in the URL makes
      // the flyout re-open every time the analyst returns to that tab.
      // Strip them on any tab switch so the deep-link is one-shot.
      p.delete("workItem");
      router.replace(`/e?${p.toString()}`, { scroll: false });
    },
    [params, router],
  );

  // v1.4.13: one-shot prefill for the Start-a-run box, set when an entity
  // quick-action fires on the Entities tab (roadmap #10). Consumed on
  // RunPrompt mount.
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);

  // v1.0.0: engagement + findings live in the React Query cache. Navigating
  // away and back is instant (cache-served) and window-focus revalidates
  // both. SSE events merge into the findings cache directly via
  // qc.setQueryData, so the "smooth status transition" pain from user
  // feedback goes away.
  const qc = useQueryClient();
  const engagementQuery = useEngagement(slug);
  const findingsQuery = useFindings(slug);
  const { data: me } = useMe();
  const engagement = engagementQuery.data ?? null;
  const findings = findingsQuery.data ?? [];
  const archiveMutation = useArchiveEngagementMutation(slug);
  const flushMutation = useFlushEngagementMutation(slug);
  const updateEngagementMutation = useUpdateEngagementMutation(slug);

  const [events, setEvents] = useState<LoggedEvent[]>([]);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<
    "connecting" | "open" | "closed"
  >("connecting");
  const [grantsRefreshKey, setGrantsRefreshKey] = useState(0);

  const seenSseIds = useRef<Set<string>>(new Set());

  const engagementErr = engagementQuery.error;
  const error =
    localError ??
    (engagementErr instanceof Error
      ? engagementErr.message
      : engagementErr
        ? String(engagementErr)
        : null);

  // Reset the ephemeral pieces when the slug changes. Query cache handles
  // the engagement + findings resets automatically (different query keys).
  useEffect(() => {
    setEvents([]);
    seenSseIds.current.clear();
  }, [slug]);

  const upsertFinding = useCallback(
    (finding: Finding) => {
      upsertFindingInCache(qc, slug, finding);
      void Promise.all([
        qc.invalidateQueries({ queryKey: qk.reportReadiness(slug) }),
        qc.invalidateQueries({ queryKey: qk.findingActivity(finding.id) }),
        qc.invalidateQueries({ queryKey: qk.entities(slug) }),
      ]);
    },
    [qc, slug],
  );
  const removeFinding = useCallback(
    (findingId: string) => {
      removeFindingFromCache(qc, slug, findingId);
      void Promise.all([
        qc.invalidateQueries({ queryKey: qk.reportReadiness(slug) }),
        qc.invalidateQueries({ queryKey: qk.entities(slug) }),
      ]);
    },
    [qc, slug],
  );

  useEffect(() => {
    const controller = new AbortController();
    setStreamState("connecting");
    subscribeToEvents({
      slug,
      signal: controller.signal,
      onOpen: () => setStreamState("open"),
      onError: () => setStreamState("closed"),
      onEvent: (event, sseId) => {
        const id = sseId ?? `local-${Date.now()}-${Math.random()}`;
        if (seenSseIds.current.has(id)) return;
        seenSseIds.current.add(id);

        setEvents((prev) =>
          [{ sseId: id, receivedAt: Date.now(), event }, ...prev].slice(0, 200),
        );

        if (event.type === "finding.created") {
          const rowId = event.finding_id || id;
          // v1.0.0: merge into the findings cache directly, so any
          // route-mounted <FindingsView> re-renders and the row persists
          // across navigation.
          qc.setQueryData<Finding[]>(qk.findings(slug), (prev) => {
            const list = prev ?? [];
            if (list.some((f) => f.id === rowId)) return list;
            const created: Finding = {
              id: rowId,
              thread_id: event.thread_id,
              tool: event.tool,
              target: event.target,
              args: event.args,
              data: event.data,
              severity: event.severity,
              title: event.title ?? event.tool,
              phase: event.phase,
              status: event.status,
              validated_at: null,
              observed_at: null,
              burp_serial_number: null,
              created_at: new Date().toISOString(),
            };
            return [created, ...list];
          });
          void Promise.all([
            qc.invalidateQueries({ queryKey: qk.reportReadiness(slug) }),
            qc.invalidateQueries({ queryKey: qk.entities(slug) }),
            qc.invalidateQueries({ queryKey: ["stored-entities", slug] }),
          ]);
        } else if (
          event.type === "run.completed" ||
          event.type === "run.errored"
        ) {
          // v1.0.0(4b): fold terminal run events into the react-query cache
          // so the analyst sees the state flip without waiting for the next
          // 2s status poll. Status view auto-re-renders; costs (which also
          // move on run end) get a fresh fetch on next mount.
          void qc.invalidateQueries({
            queryKey: qk.engagementStatus(slug),
          });
          void qc.invalidateQueries({
            queryKey: qk.engagementCosts(slug),
          });
          void qc.invalidateQueries({
            queryKey: qk.toolInvocations(slug),
          });
        } else if (event.type === "approval.pending" && canWrite) {
          void qc.invalidateQueries({ queryKey: qk.pendingApprovals() });
          setPending({
            approval_id: event.approval_id,
            thread_id: event.thread_id,
            tool: event.tool,
            args: event.args,
            risk: event.risk,
            scope: event.scope,
            tool_call_id: event.tool_call_id,
          });
        }
      },
    }).catch((err) => {
      setStreamState("closed");
      setLocalError(err instanceof Error ? err.message : String(err));
    });

    return () => {
      controller.abort();
    };
  }, [slug, canWrite, qc]);

  const onArchive = async () => {
    if (!engagement) return;
    if (!window.confirm(`Archive ${engagement.slug}? Stops new runs.`)) return;
    try {
      await archiveMutation.mutateAsync();
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  const onUnarchive = async () => {
    if (!engagement) return;
    try {
      await updateEngagementMutation.mutateAsync({ status: "active" });
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  // Token-saving kill-switch: pauses the strategic watcher (finding trigger)
  // and auto-reassess (work-item resolve) so no LLM tokens are spent on
  // auto-generated suggestions while an analyst is just evaluating. The
  // manual Analyze button is unaffected either way.
  const onToggleAutoAssess = async () => {
    if (!engagement) return;
    try {
      await updateEngagementMutation.mutateAsync({
        auto_assess_enabled: !engagement.auto_assess_enabled,
      });
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  // Hard delete: irreversibly drops the engagement row and cascades through
  // findings, scope, approvals, audit log, tasks, leases, attachments,
  // entities, observations. Type-to-confirm because there is no undo.
  const onDelete = async () => {
    if (!engagement) return;
    const typed = window.prompt(
      `Permanently delete "${engagement.slug}" and ALL of its data ` +
        `(findings, observations, scope items, audit log, attachments, ` +
        `entities, tasks). This CANNOT be undone.\n\n` +
        `Type the slug exactly to confirm:`,
    );
    if (typed === null) return;
    if (typed !== engagement.slug) {
      window.alert("Slug didn't match — no delete performed.");
      return;
    }
    try {
      await flushMutation.mutateAsync();
      router.push("/");
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  if (!engagement) {
    return (
      <p className="text-sm text-muted-foreground">
        {error ?? "Loading engagement…"}
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {/* Engagement header — full width above the workspace. */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link
            href="/"
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            ← all engagements
          </Link>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">
            {engagement.name}
          </h1>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {engagement.slug} · {engagement.status} · {formatTimeFrame(engagement)} · stream {streamState}
          </p>
          {engagement.description && (
            <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
              {engagement.description}
            </p>
          )}
        </div>
        {canWrite && (
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button asChild variant="outline" size="sm">
              <Link href={`/automation?tab=reporting&slug=${encodeURIComponent(slug)}`}>
                Build report
              </Link>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onToggleAutoAssess}
              disabled={updateEngagementMutation.isPending}
              title={
                engagement.auto_assess_enabled
                  ? "Auto-assess is on — the strategic watcher (new findings) and auto-reassess (work-item resolve) run automatically. Click to pause and stop spending tokens on auto-generated suggestions. The manual Analyze button still works either way."
                  : "Auto-assess is paused — no background strategist runs on new findings or work-item resolves, so no LLM tokens are spent on auto-generated suggestions. Click to resume. The manual Analyze button still works either way."
              }
              className="gap-1.5"
            >
              <span
                className={
                  engagement.auto_assess_enabled
                    ? "inline-block h-2 w-2 rounded-full bg-emerald-500"
                    : "inline-block h-2 w-2 rounded-full bg-muted-foreground/50"
                }
                aria-hidden
              />
              {engagement.auto_assess_enabled ? "Auto-assess on" : "Auto-assess paused"}
            </Button>
            {engagement.status === "active" && (
              <Button variant="outline" size="sm" onClick={onArchive}>
                Archive
              </Button>
            )}
            {engagement.status === "archived" && (
              <Button
                variant="outline"
                size="sm"
                onClick={onUnarchive}
                disabled={updateEngagementMutation.isPending}
              >
                {updateEngagementMutation.isPending ? "Reopening…" : "Unarchive"}
              </Button>
            )}
            {me?.is_admin && (
              <Button
                variant="outline"
                size="sm"
                onClick={onDelete}
                className="text-critical hover:bg-critical/10"
              >
                Delete
              </Button>
            )}
          </div>
        )}
      </div>

      {error && <p className="text-sm text-critical">{error}</p>}

      {/* Left nav + content pane. */}
      <div className="flex gap-8">
        <EngagementNav
          active={view}
          onSelect={setView}
          onHover={(v) => prefetchEngagementView(qc, slug, v)}
        />

        <div className="min-w-0 flex-1">
          {view === "findings" && (
            <FindingsView
              slug={slug}
              findings={findings}
              onUpdated={upsertFinding}
              onDeleted={removeFinding}
            />
          )}

          {view === "strategy" && (
            <StrategyView slug={slug} engagementStatus={engagement.status} />
          )}

          {view === "entities" && (
            <EntitiesView
              slug={slug}
              onQuickAction={(p) => {
                setPendingPrompt(p);
                setView("scope");
              }}
            />
          )}

          {view === "dossier" && <DossierView slug={slug} />}

          {view === "observations" && <ObservationsView slug={slug} />}

          {view === "costs" && <CostsView slug={slug} />}

          {view === "status" && <StatusView slug={slug} />}

          {view === "contributions" && <ContributionsView slug={slug} />}
          {view === "diagnostics" && <DiagnosticsView slug={slug} />}

          {view === "scope" && (
            <div className="space-y-6">
              <ScopeEditor slug={slug} canWrite={canWrite} />
              {engagement.status === "active" ? (
                // v1.11.0: ToolsPanel + RunPrompt share a bridge so a
                // click on a tool button drops its example prompt into
                // the run textarea below.
                // v1.15.0 (#93): entity quick-actions on the Entities
                // tab also seed the textarea via ``initialPrompt``; both
                // paths coexist because RunPrompt owns the prompt state.
                <RunPromptBridgeProvider>
                  <ToolsPanel />
                  <div className="mt-6">
                    <RunPrompt
                      slug={slug}
                      initialPrompt={pendingPrompt ?? undefined}
                      onPromptConsumed={() => setPendingPrompt(null)}
                    />
                  </div>
                </RunPromptBridgeProvider>
              ) : (
                <p className="text-sm text-muted-foreground">
                  This engagement is {engagement.status}; runs are disabled.
                </p>
              )}
              <GrantsCard
                engagementId={engagement.id}
                refreshKey={grantsRefreshKey}
                canRevoke={canWrite}
              />
            </div>
          )}
        </div>
      </div>

      {canWrite && (
        <ApprovalsModal
          pending={pending}
          onResolved={() => {
            setPending(null);
            setGrantsRefreshKey((k) => k + 1);
          }}
          onClose={() => setPending(null)}
        />
      )}
    </div>
  );
}

function EngagementGate() {
  const router = useRouter();
  const params = useSearchParams();
  const slug = params.get("slug");

  // v1.4.2: /e without a ?slug= param has no engagement to render — it
  // used to render a "Missing ?slug=" dead-end. That fell out of MSAL's
  // navigateToLoginRequestUrl behavior: an analyst who bookmarked /e (or
  // came from a stale link) would land here after Entra sign-in and get
  // stuck. Redirect to the engagement list instead so the sign-in flow
  // always ends on something useful.
  useEffect(() => {
    if (!slug) router.replace("/");
  }, [slug, router]);

  if (!slug) {
    return (
      <p className="text-sm text-muted-foreground">Loading engagements…</p>
    );
  }
  return <EngagementDetail slug={slug} />;
}

export default function EngagementDetailPage() {
  // useSearchParams() requires a Suspense boundary under static export.
  return (
    <Suspense
      fallback={<p className="text-sm text-muted-foreground">Loading…</p>}
    >
      <EngagementGate />
    </Suspense>
  );
}
