"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useRef, useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApprovalsList } from "@/components/approvals-list";
import { DownloadReport } from "@/components/download-report";
import { EventLog, type LoggedEvent } from "@/components/event-log";
import {
  FindingsTable,
  type FindingRow,
} from "@/components/findings-table";
import { GrantsCard } from "@/components/grants-card";
import { ScopeList } from "@/components/scope-list";
import { getEngagement, listFindings } from "@/lib/api";
import { subscribeToEvents } from "@/lib/events";
import { useSources } from "@/lib/source-context";
import type { Engagement } from "@/lib/types";

export default function EngagementDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const { current } = useSources();

  const [engagement, setEngagement] = useState<Engagement | null>(null);
  const [events, setEvents] = useState<LoggedEvent[]>([]);
  const [findings, setFindings] = useState<FindingRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<
    "connecting" | "open" | "closed"
  >("connecting");
  // Bumped on approval.pending / tool.auto_approved so the read-only
  // approvals + grants cards refetch (they don't subscribe to SSE directly).
  const [approvalsRefreshKey, setApprovalsRefreshKey] = useState(0);
  const [grantsRefreshKey, setGrantsRefreshKey] = useState(0);

  const seenSseIds = useRef<Set<string>>(new Set());

  const reload = useCallback(async () => {
    if (!current) return;
    try {
      setEngagement(await getEngagement(current, slug));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [current, slug]);

  // Source switch resets engagement + findings + events; nothing carries over.
  useEffect(() => {
    setEngagement(null);
    setFindings([]);
    setEvents([]);
    seenSseIds.current.clear();
    reload();
  }, [reload, current?.id]);

  // Hydrate findings from the DB on load. The SSE stream only delivers events
  // created after connect, so persisted findings would otherwise vanish on
  // reload.
  useEffect(() => {
    if (!current) return;
    let cancelled = false;
    listFindings(current, slug)
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
        // Non-fatal: the live stream still works.
      });
    return () => {
      cancelled = true;
    };
  }, [current, slug]);

  useEffect(() => {
    if (!current) return;
    const controller = new AbortController();
    setStreamState("connecting");
    subscribeToEvents({
      source: current,
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
          setApprovalsRefreshKey((k) => k + 1);
        } else if (event.type === "tool.auto_approved") {
          setGrantsRefreshKey((k) => k + 1);
        }
      },
    }).catch((err) => {
      setStreamState("closed");
      setError(err instanceof Error ? err.message : String(err));
    });

    return () => {
      controller.abort();
    };
  }, [current, slug]);

  if (!current) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a source to view this engagement.
      </p>
    );
  }

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
              <code>{streamState}</code> · source{" "}
              <code>{current.name}</code>
            </p>
          </div>
          <DownloadReport slug={slug} />
        </CardHeader>
        {error && (
          <CardContent>
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        )}
      </Card>

      <ScopeList slug={slug} />

      <GrantsCard
        engagementId={engagement.id}
        refreshKey={grantsRefreshKey}
      />

      <ApprovalsList slug={slug} refreshKey={approvalsRefreshKey} />

      <FindingsTable findings={findings} />
      <EventLog events={events} />
    </div>
  );
}
