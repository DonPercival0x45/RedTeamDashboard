"use client";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { Severity } from "@/lib/types";

export interface FindingRow {
  id: string; // SSE message id or DB row id
  thread_id: string;
  tool: string;
  target: string | null;
  severity: Severity;
  title: string | null;
  args: Record<string, unknown>;
  data: Record<string, unknown>;
}

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
  info: 0,
};

const SEVERITY_STYLES: Record<Severity, string> = {
  critical: "border-red-600 bg-red-50 text-red-700",
  high: "border-orange-600 bg-orange-50 text-orange-700",
  medium: "border-amber-500 bg-amber-50 text-amber-700",
  low: "border-sky-500 bg-sky-50 text-sky-700",
  info: "border-slate-400 bg-slate-50 text-slate-600",
};

export function FindingsTable({ findings }: { findings: FindingRow[] }) {
  // High-severity rows first; preserve original ordering inside a severity band.
  const sorted = [...findings].sort(
    (a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity],
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Findings</CardTitle>
        <CardDescription>
          Loaded from saved findings, then updated live from{" "}
          <code>finding.created</code> events.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {findings.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No findings yet for this engagement.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase text-muted-foreground">
                  <th className="px-3 py-2 w-20">Severity</th>
                  <th className="px-3 py-2 w-28">Tool</th>
                  <th className="px-3 py-2">Title</th>
                  <th className="px-3 py-2">Target</th>
                  <th className="px-3 py-2">Data</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((finding) => (
                  <tr key={finding.id} className="border-b align-top">
                    <td className="px-3 py-2">
                      <Badge
                        variant="outline"
                        className={SEVERITY_STYLES[finding.severity]}
                      >
                        {finding.severity}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {finding.tool}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {finding.title ?? "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {finding.target ??
                        (Object.values(finding.args).join(", ") || "—")}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">
                      <pre className="whitespace-pre-wrap">
                        {JSON.stringify(finding.data, null, 2)}
                      </pre>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
