"use client";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { RunEvent } from "@/lib/types";

interface LoggedEvent {
  sseId: string;
  receivedAt: number;
  event: RunEvent;
}

const COLORS: Record<RunEvent["type"], string> = {
  "run.started": "border-blue-500 text-blue-700",
  "approval.pending": "border-amber-500 text-amber-700",
  "tool.denied": "border-orange-500 text-orange-700",
  "tool.auto_approved": "border-violet-500 text-violet-700",
  "finding.created": "border-emerald-500 text-emerald-700",
  "run.completed": "border-slate-500 text-slate-700",
  "run.errored": "border-red-500 text-red-700",
};

function summarize(event: RunEvent): string {
  switch (event.type) {
    case "run.started":
      return event.prompt;
    case "approval.pending":
      return `${event.tool} (${event.risk}) — ${JSON.stringify(event.args)}`;
    case "tool.denied":
      return `${event.tool} ${JSON.stringify(event.args)} — ${event.reason}`;
    case "tool.auto_approved":
      return `${event.tool} ${JSON.stringify(event.args)} — auto-approved (session grant)`;
    case "finding.created":
      return `${event.tool} → ${JSON.stringify(event.data).slice(0, 140)}`;
    case "run.completed":
      return `thread ${event.thread_id.slice(0, 8)}…`;
    case "run.errored":
      return event.error;
  }
}

export function EventLog({ events }: { events: LoggedEvent[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Event log</CardTitle>
        <CardDescription>
          Live tail of <code>runs:&lt;eid&gt;:events</code>.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {events.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Waiting for events. Start a run above.
          </p>
        ) : (
          <ul className="space-y-2 font-mono text-xs">
            {events.map((entry) => (
              <li
                key={entry.sseId}
                className="flex items-start gap-3 rounded border-l-2 bg-muted/40 px-3 py-2"
              >
                <Badge
                  variant="outline"
                  className={COLORS[entry.event.type] ?? ""}
                >
                  {entry.event.type}
                </Badge>
                <span className="break-all">{summarize(entry.event)}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export type { LoggedEvent };
