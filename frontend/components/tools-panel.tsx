"use client";

// v1.11.0: Scope-tab "Current Tools" panel.
//
// Sits above the <RunPrompt> textarea on the engagement Scope tab.
// Renders the first-party tool catalog grouped by task_kind
// (Enumeration / Scanning / Analyst-only). Each tool is a compact
// pill button; clicking one calls into the RunPromptBridge to drop
// the tool's example_prompt (falling back to "Run <name> against an
// in-scope target." when no curated example ships).
//
// Reads through the same useDefaultTools cache as the Settings > Tools
// banner — the two surfaces share the query so a change on one
// invalidates both.

import { Wrench } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useDefaultTools } from "@/lib/hooks";
import { groupToolsByPhase, toolPromptOrFallback } from "@/lib/tool-phases";
import { useRunPromptBridge } from "@/components/run-prompt-context";
import type { ToolRead } from "@/lib/types";

export function ToolsPanel() {
  const { data: tools, error } = useDefaultTools();
  const bridge = useRunPromptBridge();

  if (error) return null; // silent — the run prompt below still works
  const grouped = groupToolsByPhase(tools ?? []);
  const nothing = (tools ?? []).length === 0;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Wrench className="h-4 w-4" />
          Current tools
        </CardTitle>
        <CardDescription className="text-xs">
          Ships with the install. Click a tool to load its example prompt
          into the run box below — edit before hitting Run.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 pb-4">
        {nothing && (
          <p className="text-xs text-muted-foreground">
            No first-party tools registered on this install.
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
                    key={t.id}
                    tool={t}
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
  onPick,
}: {
  tool: ToolRead;
  onPick: (text: string) => void;
}) {
  const prompt = toolPromptOrFallback(tool);
  return (
    <button
      type="button"
      onClick={() => onPick(prompt)}
      title={tool.description ?? prompt}
      className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs transition-colors hover:border-foreground/40 hover:bg-secondary"
    >
      <span className="font-medium">{tool.name}</span>
      <span className="text-[10px] text-muted-foreground/70">{tool.kind}</span>
    </button>
  );
}
