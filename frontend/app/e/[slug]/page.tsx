"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EventLog, type LoggedEvent } from "@/components/event-log";
import {
  ApprovalsModal,
  type PendingApproval,
} from "@/components/approvals-modal";
import {
  FindingsTable,
  type FindingRow,
} from "@/components/findings-table";
import { DownloadReport } from "@/components/download-report";
import { GrantsCard } from "@/components/grants-card";
import { RunPrompt } from "@/components/run-prompt";
import { ScopeEditor } from "@/components/scope-editor";
import { archiveEngagement, getEngagement, listFindings } from "@/lib/api";
import { subscribeToEvents } from "@/lib/events";
import type { Engagement, RunEvent } from "@/lib/types";

export default function EngagementDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);

  const [engagement, setEngagement] = useState<Engagement | null>(null);
  const [events, setEvents] = useState<LoggedEvent[]>([]);
  const [findings, setFindings] = useState<FindingRow[]>([]);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<"connecting" | "open" | "closed">(
    "connecting",
  );
  // Bumped after an approval modal closes (a new grant may have been created
  // with "remember") so the grants card refetches.
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
    reload();
  }, [reload]);

  // Hydrate findings from the DB on load. The SSE stream only delivers events
  // created after connect (server starts at `$`), so persisted findings would
  // otherwise vanish on reload. Past (DB) and live (SSE) don't overlap; merge
  // by id and keep any live findings that already arrived in front.
  useEffect(() => {
    let cancelled = false;
    listFindings(slug)
      .then((rows) => {
        if (cancelled) return;
        const hydrated: FindingRow[] = rows.map((r) => ({
          id: r.id,
          thread_id: r.thread_id ?? "",
          tool: r.tool ?? "",
          target: r.target,
          severity: r.severity,
          title: r.title,
          args: r.args ?? {},
          data: r.data ?? {},
        }));
        setFindings((prev) => {
          const seen = new Set(prev.map((f) => f.id));
          return [...prev, ...hydrated.filter((f) => !seen.has(f.id))];
        });
      })
      .catch(() => {
        // Non-fatal: the live stream still works; findings just won't backfill.
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

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

        setEvents((prev) => [
          { sseId: id, receivedAt: Date.now(), event },
          ...prev,
        ].slice(0, 200));

        if (event.type === "finding.created") {
          setFindings((prev) => {
            // Worker now stamps a finding_id; prefer it so the live row matches
            // the eventual DB hydration and we don't double-render after reload.
            const rowId = event.finding_id || id;
            if (prev.some((f) => f.id === rowId)) return prev;
            return [
              {
                id: rowId,
                thread_id: event.thread_id,
                tool: event.tool,
                target: event.target,
                severity: event.severity,
                title: event.title,
                args: event.args,
                data: event.data,
              },
              ...prev,
            ];
          });
        } else if (event.type === "approval.pending") {
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
  }, [slug]);

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

  if (!engagement) {
    return (
      <p className="text-sm text-muted-foreground">
        {error ?? "Loading engagement…"}
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between space-y-0">
          <div>
            <Link
              href="/"
              className="text-xs text-muted-foreground hover:underline"
            >
              ← all engagements
            </Link>
            <CardTitle className="mt-2">{engagement.name}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              slug <code>{engagement.slug}</code> · status{" "}
              <code>{engagement.status}</code> · stream{" "}
              <code>{streamState}</code>
            </p>
          </div>
          <div className="flex items-start gap-2">
            <DownloadReport slug={slug} />
            {engagement.status === "active" && (
              <Button variant="outline" size="sm" onClick={onArchive}>
                Archive
              </Button>
            )}
          </div>
        </CardHeader>
        {error && (
          <CardContent>
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        )}
      </Card>

      <ScopeEditor slug={slug} />

      <GrantsCard engagementId={engagement.id} refreshKey={grantsRefreshKey} />

      {engagement.status === "active" ? (
        <RunPrompt slug={slug} />
      ) : (
        <p className="text-sm text-muted-foreground">
          This engagement is {engagement.status}; runs are disabled.
        </p>
      )}

      <FindingsTable findings={findings} />
      <EventLog events={events} />

      <ApprovalsModal
        pending={pending}
        onResolved={() => {
          setPending(null);
          setGrantsRefreshKey((k) => k + 1);
        }}
      />
    </div>
  );
}
