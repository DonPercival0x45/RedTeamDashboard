// v1.11.0: shared taxonomy for grouping tools by task_kind on the
// Settings > Tools banner and the Scope-tab "Current Tools" panel.
//
// task_kind is the charter-gate taxonomy — the agent dispatcher enforces
// enum/scan-only; exploit-kind tools are analyst-only regardless of
// lane. Same three buckets show up in the UI so analysts immediately
// see what an agent will and won't reach for on their behalf.
//
// v1.12.0: switched from ``ToolRead`` (analyst-uploaded catalog) to
// ``OrchestratorTool`` (built-in FastMCP registry — subfinder,
// dns_lookup, port_scan, etc.). The two surfaces (Settings banner +
// Scope-tab panel) always want the built-ins; there's no case where
// the analyst-upload catalog belongs in either.

import type { OrchestratorTool, ToolTaskKind } from "@/lib/types";

export interface ToolPhaseMeta {
  key: ToolTaskKind;
  label: string;
  hint: string;
}

export const TOOL_PHASES: ToolPhaseMeta[] = [
  {
    key: "enum",
    label: "Enumeration",
    hint: "Passive discovery — agents dispatch these on their own.",
  },
  {
    key: "scan",
    label: "Scanning",
    hint: "Active probes — agents dispatch inside the approved scope.",
  },
  {
    key: "exploit",
    label: "Analyst-only",
    hint: "Validation / proof-of-concept — analyst dispatches manually.",
  },
];

export interface ToolsByPhase {
  phase: ToolPhaseMeta;
  tools: OrchestratorTool[];
}

// Group + sort tools into TOOL_PHASES order. Empty phases are kept so
// the UI can either show "nothing seeded here" hints or hide them.
export function groupToolsByPhase(
  tools: readonly OrchestratorTool[],
): ToolsByPhase[] {
  return TOOL_PHASES.map((phase) => ({
    phase,
    tools: tools
      .filter((t) => t.kind === phase.key)
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name)),
  }));
}

// Fallback rendered on the Scope panel when a tool has no curated
// example_prompt. Deliberately generic — encourages tool authors to
// ship a real one but doesn't leave the button empty. Orchestrator
// tools always ship an example_prompt (backend fills one for known
// tools + generic fallback for the rest) so this is mostly defensive.
export function toolPromptOrFallback(tool: OrchestratorTool): string {
  if (tool.example_prompt && tool.example_prompt.trim().length > 0) {
    return tool.example_prompt.trim();
  }
  return `Run ${tool.name} against an in-scope target.`;
}
