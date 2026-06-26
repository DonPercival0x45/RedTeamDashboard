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
import { EventLog, type LoggedEvent } from "@/components/event-log";
import {
  ProjectNav,
  type ProjectView,
} from "@/components/Project-nav";
import { EntitiesView } from "@/components/entities-view";
import { FindingsView } from "@/components/findings-view";
import { ObservationsView } from "@/components/observations-view";
import { CostsView } from "@/components/costs-view";
import { GrantsCard } from "@/components/grants-card";
import { RunPrompt } from "@/components/run-prompt";
import { ScopeEditor } from "@/components/scope-editor";
import { archiveProject, downloadProjectExport, getProject, listFindings } from "@/lib/api";
import { subscribeToEvents } from "@/lib/events";
import type { Project, Finding } from "@/lib/types";

// Slug + active view ride in the query string (?slug=&view=) so the page can be
// statically exported for Azure SWA (no dynamic route segments). The Project
// opens on Findings — the work product is front and center (see CHARTER).

function ReportView({ slug }: { slug: string }) {
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const onExportJSON = async () => {
    setExportBusy(true);
    setExportError(null);
    try {
      await downloadProjectExport(slug);
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
          <span className="text-critical">●</span> PDF includes the Project&apos;s{" "}
          <strong>validated</strong> findings across every phase — including any
          summaries written in finding detail panels. JSON export includes the
          full snapshot (findings, scope, observations, audit summary).
        </p>
      </CardContent>
    </Card>
  );
}

const VALID_VIEWS = new Set<ProjectView>([
  "findings",
  "entities",
  "observations",
  "report",
  "costs",
  "scope",
]);

function EngagementDetail({ slug }: { slug: string }) {
  const router = useRouter();
  const params = useSearchParams();
  // Single-tenant: any signed-in analyst can act on the Project.
  const canWrite = true;

  const viewParam = params.get("view");
  const view: ProjectView =
    viewParam && VALID_VIEWS.has(viewParam as ProjectView)
      ? (viewParam as ProjectView)
      : "findings";
  const setView = useCallback(
    (next: ProjectView) => {
      const p = new URLSearchParams(params.toString());
      p.set("view", next);
      router.replace(`/e?${p.toString()}`, { scroll: false });
    },
    [params, router],
  );

  const [Project, setEngagement] = useState<Project | null>(null);
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
      setEngagement(await getProject(slug));
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
    if (!Project) return;
    if (!window.confirm(`Archive ${Project.slug}? Stops new runs.`)) return;
    try {
      await archiveProject(slug);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (!Project) {
    return (
      <p className="text-sm text-muted-foreground">
        {error ?? "Loading Project…"}
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {/* Project header — full width above the workspace. */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link
            href="/"
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            ← all engagements
          </Link>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">
            {Project.name}
          </h1>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {Project.slug} · {Project.status} · stream {streamState}
          </p>
          {Project.description && (
            <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
              {Project.description}
            </p>
          )}
        </div>
        {canWrite && Project.status === "active" && (
          <Button variant="outline" size="sm" onClick={onArchive}>
            Archive
          </Button>
        )}
      </div>

      {error && <p className="text-sm text-critical">{error}</p>}

      {/* Left nav + content pane. */}
      <div className="flex gap-8">
        <ProjectNav active={view} onSelect={setView} />

        <div className="min-w-0 flex-1">
          {view === "findings" && (
            <FindingsView slug={slug} findings={findings} onUpdated={upsertFinding} />
          )}

          {view === "entities" && <EntitiesView slug={slug} />}

          {view === "observations" && <ObservationsView slug={slug} />}

          {view === "report" && (
            <ReportView slug={slug} />
          )}

          {view === "costs" && <CostsView slug={slug} />}

          {view === "scope" && (
            <div className="space-y-6">
              <ScopeEditor slug={slug} canWrite={canWrite} />
              {Project.status === "active" ? (
                <RunPrompt slug={slug} />
              ) : (
                <p className="text-sm text-muted-foreground">
                  This Project is {Project.status}; runs are disabled.
                </p>
              )}
              <GrantsCard
                engagementId={Project.id}
                refreshKey={grantsRefreshKey}
                canRevoke={canWrite}
              />
              <EventLog events={events} />
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
