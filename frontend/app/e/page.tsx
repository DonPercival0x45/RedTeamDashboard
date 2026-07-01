"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
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
import { ToolsView } from "@/components/tools-view";
import { GrantsCard } from "@/components/grants-card";
import { RunPrompt } from "@/components/run-prompt";
import { ScopeEditor } from "@/components/scope-editor";
import {
  archiveEngagement,
  downloadEngagementExport,
  flushEngagement,
  getEngagement,
  listFindings,
} from "@/lib/api";
import { subscribeToEvents } from "@/lib/events";
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
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const onExportJSON = async () => {
    setExportBusy(true);
    setExportError(null);
    try {
      await downloadEngagementExport(slug);
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
          <DownloadReport slug={slug} />
        </div>
      </CardHeader>
      <CardContent>
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

  const [engagement, setEngagement] = useState<Engagement | null>(null);
  const [events, setEvents] = useState<LoggedEvent[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<
    "connecting" | "open" | "closed"
  >("connecting");
  const [grantsRefreshKey, setGrantsRefreshKey] = useState(0);

  const seenSseIds = useRef<Set<string>>(new Set());

  const reload = useCallback(async () => {
    try {
      setEngagement(await getEngagement(slug));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [slug]);

  useEffect(() => {
    setEngagement(null);
    setFindings([]);
    setEvents([]);
    seenSseIds.current.clear();
    reload();
  }, [reload]);

  useEffect(() => {
    let cancelled = false;
    listFindings(slug)
      .then((rows) => {
        if (cancelled) return;
        setFindings((prev) => {
          const seen = new Set(prev.map((f) => f.id));
          return [...prev, ...rows.filter((f) => !seen.has(f.id))];
        });
      })
      .catch(() => {
        // Non-fatal: the live stream still works.
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  // Merge a validated/updated finding back into the list.
  const upsertFinding = useCallback((f: Finding) => {
    setFindings((prev) => {
      const idx = prev.findIndex((x) => x.id === f.id);
      if (idx === -1) return [f, ...prev];
      const next = [...prev];
      next[idx] = f;
      return next;
    });
  }, []);

  // v0.10.0: drop a soft-deleted finding from the list so the analyst
  // sees it disappear without a refetch.
  const removeFinding = useCallback((findingId: string) => {
    setFindings((prev) => prev.filter((f) => f.id !== findingId));
  }, []);

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
          setFindings((prev) => {
            if (prev.some((f) => f.id === rowId)) return prev;
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
            return [created, ...prev];
          });
        } else if (event.type === "approval.pending" && canWrite) {
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
      setError(err instanceof Error ? err.message : String(err));
    });

    return () => {
      controller.abort();
    };
  }, [slug, canWrite]);

  const onArchive = async () => {
    if (!engagement) return;
    if (!window.confirm(`Archive ${engagement.slug}? Stops new runs.`)) return;
    try {
      await archiveEngagement(slug);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
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
      await flushEngagement(slug);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
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
        <EngagementNav active={view} onSelect={setView} />

        <div className="min-w-0 flex-1">
          {view === "findings" && (
            <FindingsView
              slug={slug}
              findings={findings}
              onUpdated={upsertFinding}
              onDeleted={removeFinding}
            />
          )}

          {view === "entities" && <EntitiesView slug={slug} />}

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
                <RunPrompt slug={slug} />
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
        />
      )}
    </div>
  );
}

function EngagementGate() {
  const params = useSearchParams();
  const slug = params.get("slug");
  if (!slug) {
    return (
      <p className="text-sm text-muted-foreground">
        Missing <code>?slug=</code> parameter. Go back to{" "}
        <Link href="/" className="underline">
          engagements
        </Link>
        .
      </p>
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
