"use client";

// v1.11.0: Scope-tab "Current Tools" panel.
// v1.12.0: source is the orchestrator FastMCP registry (subfinder,
// dns_lookup, port_scan, …) instead of the analyst-upload catalog.
// v2.24.1: tools with needs_secret=true get a "key ready" (green) or
// "needs key" (amber) pill based on whether the analyst has uploaded
// a matching provider at /settings/keys. Clicking a "needs key" tool
// still drops the prompt so the analyst can proceed after uploading.
//
// Sits above the <RunPrompt> textarea on the engagement Scope tab.
// Renders the built-in tool catalog grouped by task_kind (Enumeration /
// Scanning / Analyst-only). Each tool is a compact pill button;
// clicking one calls into the RunPromptBridge to drop the tool's
// example_prompt (falling back to "Run <name> against an in-scope
// target." when no curated example ships).
//
// Reads through the same useDefaultTools cache as the Settings > Tools
// banner — the two surfaces share the query so a change on one
// invalidates both.

import { KeyRound, Wrench } from "lucide-react";
import Link from "next/link";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useDefaultTools, useProviderKeys } from "@/lib/hooks";
import { groupToolsByPhase, toolPromptOrFallback } from "@/lib/tool-phases";
import { useRunPromptBridge } from "@/components/run-prompt-context";
import type { OrchestratorTool } from "@/lib/types";

export function ToolsPanel() {
  const { data: tools, error } = useDefaultTools();
  const { data: providerKeys } = useProviderKeys();
  const bridge = useRunPromptBridge();

  if (error) return null; // silent — the run prompt below still works
  const grouped = groupToolsByPhase(tools ?? []);
  const nothing = (tools ?? []).length === 0;
  // v2.24.1: index uploaded provider keys by provider slug so we can
  // quickly answer "does the analyst have a key for this tool?"
  const keyedProviders = new Set(
    (providerKeys ?? []).map((k) => k.provider.toLowerCase()),
  );

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Wrench className="h-4 w-4" />
          Current tools
        </CardTitle>
        <CardDescription className="text-xs">
          Ships with the install. Click a tool to load its example prompt
          into the run box below — edit before hitting Run. Tools marked{" "}
          <KeyRound className="inline h-3 w-3" /> need a BYO API key from{" "}
          <Link href="/settings/keys" className="underline hover:text-foreground">
            /settings/keys
          </Link>
          .
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 pb-4">
        {nothing && (
          <p className="text-xs text-muted-foreground">
            No built-in tools registered on this backend image.
          </p>
        )}
        {grouped.map(({ phase, tools: phaseTools }) => {
          if (phaseTools.length === 0) return null;
          return (
            <div key={phase.key}>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {phase.label}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {phaseTools.map((t) => (
                  <ToolButton
                    key={t.name}
                    tool={t}
                    hasKey={keyedProviders.has(t.name.toLowerCase())}
                    onPick={(text) => bridge.insert(text)}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

function ToolButton({
  tool,
  hasKey,
  onPick,
}: {
  tool: OrchestratorTool;
  hasKey: boolean;
  onPick: (text: string) => void;
}) {
  const prompt = toolPromptOrFallback(tool);
  const needsKey = tool.needs_secret === true;
  const keyStatus: "none" | "ready" | "missing" = !needsKey
    ? "none"
    : hasKey
      ? "ready"
      : "missing";
  const title = needsKey
    ? hasKey
      ? `${tool.description || prompt}\n\nKey uploaded ✔ — ready to dispatch.`
      : `${tool.description || prompt}\n\nNeeds a BYO API key at /settings/keys (provider=${tool.name}).`
    : tool.description || prompt;
  return (
    <button
      type="button"
      onClick={() => onPick(prompt)}
      title={title}
      className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs transition-colors hover:border-foreground/40 hover:bg-secondary"
    >
      <span className="font-medium">{tool.name}</span>
      {keyStatus === "ready" && (
        <KeyRound className="h-3 w-3 text-emerald-500" aria-label="key uploaded" />
      )}
      {keyStatus === "missing" && (
        <KeyRound className="h-3 w-3 text-amber-500" aria-label="needs key" />
      )}
      <span className="text-[10px] text-muted-foreground/70">{tool.phase}</span>
    </button>
  );
}
