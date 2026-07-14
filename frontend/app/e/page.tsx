"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ApprovalsModal,
  type PendingApproval,
} from "@/components/approvals-modal";
import { DownloadReport } from "@/components/download-report";
import type { LoggedEvent } from "@/lib/types";
import {
  EngagementNav,
  type EngagementView,
} from "@/components/engagement-nav";
import { EntitiesView } from "@/components/entities-view";
import { FindingsView } from "@/components/findings-view";
import { ObservationsView } from "@/components/observations-view";
import { CostsView } from "@/components/costs-view";
import { ContributionsView } from "@/components/contributions-view";
import { StatusView } from "@/components/status-view";
import { StrategyView } from "@/components/strategy-view";
import { ToolsView } from "@/components/tools-view";
import { GrantsCard } from "@/components/grants-card";
import { RunPrompt } from "@/components/run-prompt";
import { RunPromptBridgeProvider } from "@/components/run-prompt-context";
import { ScopeEditor } from "@/components/scope-editor";
import { ToolsPanel } from "@/components/tools-panel";
import { downloadEngagementExport } from "@/lib/api";
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
  useReportReadiness,
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

function ReportView({ slug }: { slug: string }) {
  const readinessQuery = useReportReadiness(slug);
  const readiness = readinessQuery.data;
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  // v1.4.0: analyst toggles whether findings marked out_of_scope /
  // outside_roe show up in the exported PDF + JSON. Kept local — this
  // isn't a per-user preference, it's a per-download choice the analyst
  // makes each time they cut a deliverable.
  const [omitExcluded, setOmitExcluded] = useState(false);

  const onExportJSON = async () => {
    setExportBusy(true);
    setExportError(null);
    try {
      await downloadEngagementExport(slug, { omitExcluded });
    } catch (err) {
      setExportError(err instanceof Error ? err.message : String(err));
    } finally {
      setExportBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle>Report</CardTitle>
        <div className="flex gap-2">
          <div className="flex flex-col items-end gap-1">
            <button
              type="button"
              onClick={onExportJSON}
              disabled={exportBusy}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-xs hover:bg-secondary disabled:opacity-50"
            >
              {exportBusy ? "Exporting…" : "Export JSON"}
            </button>
            {exportError && (
              <p className="text-xs text-destructive">{exportError}</p>
            )}
          </div>
          <DownloadReport slug={slug} omitExcluded={omitExcluded} />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <section className="rounded-lg border border-border bg-background/40 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span className={`h-2.5 w-2.5 rounded-full ${readiness?.ready ? "bg-emerald-500" : "bg-amber-500"}`} />
                <h3 className="text-sm font-semibold">
                  {readinessQuery.isLoading
                    ? "Checking report readiness…"
                    : readiness?.ready
                      ? "Ready for client review"
                      : "Report needs attention"}
                </h3>
              </div>
              {readiness && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {readiness.reportable_count} reportable · {readiness.total_findings} total findings
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={() => void readinessQuery.refetch()}
              className="text-xs text-muted-foreground hover:text-foreground hover:underline"
            >
              Refresh preflight
            </button>
          </div>

          {readinessQuery.error && (
            <p className="mt-3 text-xs text-destructive">Could not load report readiness.</p>
          )}
          {readiness && (
            <ul className="mt-3 grid gap-2 sm:grid-cols-2">
              {readiness.checks
                .filter((check) => check.count > 0)
                .map((check) => {
                  const view = check.target_view?.split("&", 1)[0] ?? "report";
                  const tone = check.level === "blocker"
                    ? "border-rose-500/40 bg-rose-500/10"
                    : check.level === "warning"
                      ? "border-amber-500/40 bg-amber-500/10"
                      : "border-sky-500/40 bg-sky-500/10";
                  return (
                    <li key={check.key} className={`rounded-md border p-3 ${tone}`}>
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{check.level}</p>
                          <p className="mt-1 text-xs">{check.message}</p>
                        </div>
                        {check.target_view && (
                          <Link
                            href={`/e?slug=${encodeURIComponent(slug)}&view=${encodeURIComponent(view)}`}
                            className="shrink-0 text-xs underline"
                          >
                            Review
                          </Link>
                        )}
                      </div>
                    </li>
                  );
                })}
            </ul>
          )}
          {readiness?.ready && (
            <p className="mt-3 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-700 dark:text-emerald-200">
              No report blockers remain. Warnings are advisory and exports remain analyst controlled.
            </p>
          )}
        </section>

        <label className="flex cursor-pointer items-start gap-2 rounded-md border border-border bg-background/40 p-3 text-sm">
          <input
            type="checkbox"
            checked={omitExcluded}
            onChange={(e) => setOmitExcluded(e.target.checked)}
            className="mt-0.5 cursor-pointer accent-critical"
          />
          <div>
            <span className="font-medium">Omit excluded findings</span>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Drop findings marked <em>Out of scope</em> or <em>Outside ROE</em>{" "}
              from the PDF and JSON export. Use this when cutting a
              client-ready deliverable; leave it off for the internal
              full-record archive.
            </p>
          </div>
        </label>
        <p className="text-xs text-muted-foreground/70">
          <span className="text-critical">●</span> PDF includes the engagement&apos;s{" "}
          <strong>validated</strong> findings across every phase — including any
          summaries written in finding detail panels. JSON export includes the
          full snapshot (findings, scope, observations, audit summary).
        </p>
      </CardContent>
    </Card>
  );
}

const VALID_VIEWS = new Set<EngagementView>([
  "findings",
  "strategy",
  "entities",
  "observations",
  "report",
  "costs",
  "scope",
  "status",
  "contributions",
  "tools",
]);

function EngagementDetail({ slug }: { slug: string }) {
  const router = useRouter();
  const params = useSearchParams();
  // Single-tenant: any signed-in analyst can act on the engagement.
  const canWrite = true;

  const viewParam = params.get("view");
  const view: EngagementView =
    viewParam && VALID_VIEWS.has(viewParam as EngagementView)
      ? (viewParam as EngagementView)
      : "findings";
  const setView = useCallback(
    (next: EngagementView) => {
      const p = new URLSearchParams(params.toString());
      p.set("view", next);
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
  const engagement = engagementQuery.data ?? null;
  const findings = findingsQuery.data ?? [];
  const archiveMutation = useArchiveEngagementMutation(slug);
  const flushMutation = useFlushEngagementMutation(slug);

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
    (f: Finding) => upsertFindingInCache(qc, slug, f),
    [qc, slug],
  );
  const removeFinding = useCallback(
    (findingId: string) => removeFindingFromCache(qc, slug, findingId),
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
          <div className="flex gap-2">
            {engagement.status === "active" && (
              <Button variant="outline" size="sm" onClick={onArchive}>
                Archive
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={onDelete}
              className="text-critical hover:bg-critical/10"
            >
              Delete
            </Button>
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

          {view === "observations" && <ObservationsView slug={slug} />}

          {view === "report" && (
            <ReportView slug={slug} />
          )}

          {view === "costs" && <CostsView slug={slug} />}

          {view === "status" && <StatusView slug={slug} events={events} />}

          {view === "contributions" && <ContributionsView slug={slug} />}

          {view === "tools" && <ToolsView slug={slug} />}

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
