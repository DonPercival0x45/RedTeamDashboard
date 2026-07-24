"use client";

// v3.0.3 — Scope-tab "Current tools" panel for v3 engagements.
// Replaces the legacy ToolsPanel/RunPrompt flow (which dropped an
// example prompt into a textarea for LLM-driven dispatch). Here, each
// tool button posts directly to POST /engagements/{slug}/tools/{slug}/run
// which executes the playbook tool deterministically, writes a Finding
// row via the same grouping helper the playbook runner uses, and
// returns an outcome the analyst can act on immediately.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Play, Wrench } from "lucide-react";
import { useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { runToolDirect, type ToolRunResult } from "@/lib/api";
import { qk } from "@/lib/hooks";

// v3-bridgeable playbook tools. Kept as a static frontend catalog because
// the backend list is stable (mapped 1:1 to services/playbook/tools/).
// If a new tool is added there + entered into TOOL_ALIASES in
// finding_bridge.py, add a row here.
type V3ToolSpec = {
  slug: string;
  label: string;
  description: string;
  phase: "OSINT" | "Recon";
  stub: boolean;
};

const V3_TOOLS: V3ToolSpec[] = [
  {
    slug: "whois",
    label: "WHOIS",
    description:
      "Registrar / registrant / creation date lookup. Runs against the engagement's primary in-scope domain.",
    phase: "OSINT",
    stub: false,
  },
  {
    slug: "dns_inventory",
    label: "DNS records",
    description:
      "A / AAAA / MX / TXT / NS enumeration via dnspython. Records populate the dns_records row grouped under the apex.",
    phase: "OSINT",
    stub: false,
  },
  {
    slug: "subfinder",
    label: "Subfinder",
    description:
      "Passive subdomain enumeration. Currently a stub — real Go binary lands after data-source pick.",
    phase: "Recon",
    stub: true,
  },
  {
    slug: "crtsh",
    label: "crt.sh",
    description:
      "Certificate-transparency subdomain discovery. Currently a stub pending egress policy for outbound crt.sh calls.",
    phase: "Recon",
    stub: true,
  },
];

type LastRun = {
  ok: boolean;
  findingsNew: number;
  findingsTotal: number;
  stub: boolean;
  error: string | null;
  scope: string;
};

export function V3ToolsPanel({ slug }: { slug: string }) {
  const qc = useQueryClient();
  const [runningSlug, setRunningSlug] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<Record<string, LastRun>>({});

  const mutation = useMutation({
    mutationFn: ({
      toolSlug,
    }: {
      toolSlug: string;
    }): Promise<ToolRunResult> => runToolDirect(slug, toolSlug),
    onMutate: ({ toolSlug }) => setRunningSlug(toolSlug),
    onSuccess: (result) => {
      setLastRun((prev) => ({
        ...prev,
        [result.tool]: {
          ok: result.ok,
          findingsNew: result.findings_new,
          findingsTotal: result.findings_total,
          stub: result.stub,
          error: result.error,
          scope: result.scope,
        },
      }));
      qc.invalidateQueries({ queryKey: qk.findings(slug) });
    },
    onError: (err: unknown, vars) => {
      const message = err instanceof Error ? err.message : "unknown error";
      setLastRun((prev) => ({
        ...prev,
        [vars.toolSlug]: {
          ok: false,
          findingsNew: 0,
          findingsTotal: 0,
          stub: false,
          error: message,
          scope: "",
        },
      }));
    },
    onSettled: () => setRunningSlug(null),
  });

  const byPhase = V3_TOOLS.reduce<Record<string, V3ToolSpec[]>>((acc, t) => {
    (acc[t.phase] ??= []).push(t);
    return acc;
  }, {});

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Wrench className="h-4 w-4" />
          Current tools
        </CardTitle>
        <CardDescription className="text-xs">
          Click a tool to run it against the engagement&apos;s primary
          in-scope target. Results write directly to the Findings tab —
          no LLM, no strategist prompt.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 pb-4">
        {Object.entries(byPhase).map(([phase, tools]) => (
          <div key={phase}>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              {phase}
            </div>
            <div className="flex flex-wrap gap-2">
              {tools.map((t) => {
                const busy = runningSlug === t.slug;
                const result = lastRun[t.slug];
                return (
                  <div key={t.slug} className="flex flex-col gap-0.5">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => mutation.mutate({ toolSlug: t.slug })}
                      title={t.description}
                      className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs transition-colors hover:border-foreground/40 hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <Play className="h-3 w-3" />
                      <span className="font-medium">{t.label}</span>
                      <span className="text-[10px] text-muted-foreground/70">
                        {t.slug}
                      </span>
                      {t.stub && (
                        <span className="rounded bg-amber-500/15 px-1 text-[9px] font-medium uppercase text-amber-600 dark:text-amber-400">
                          stub
                        </span>
                      )}
                      {busy && (
                        <span className="text-[10px] text-muted-foreground">
                          running…
                        </span>
                      )}
                    </button>
                    {result && !busy && (
                      <div
                        className={`flex items-center gap-1 text-[10px] ${
                          result.ok
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-red-600 dark:text-red-400"
                        }`}
                      >
                        {result.ok ? (
                          <CheckCircle2 className="h-3 w-3" />
                        ) : (
                          <AlertCircle className="h-3 w-3" />
                        )}
                        {result.ok ? (
                          result.stub ? (
                            <span>stub — no finding written</span>
                          ) : (
                            <span>
                              {result.findingsNew} new · {result.findingsTotal}{" "}
                              total on {result.scope}
                            </span>
                          )
                        ) : (
                          <span title={result.error ?? undefined}>
                            failed{result.error ? `: ${result.error}` : ""}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
