// v1.11.0: shared taxonomy for grouping tools by task_kind on the
// Settings > Tools banner and the Scope-tab "Current Tools" panel.
//
// task_kind on Tool is the charter-gate taxonomy — the agent dispatcher
// enforces enum/scan-only; exploit-kind tools are analyst-only regardless
// of lane. Same three buckets show up in the UI so analysts immediately
// see what an agent will and won't reach for on their behalf.

import type { ToolRead, ToolTaskKind } from "@/lib/types";

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
  tools: ToolRead[];
}

// Group + sort tools into TOOL_PHASES order. Empty phases are kept so
// the UI can either show "nothing seeded here" hints or hide them.
export function groupToolsByPhase(tools: readonly ToolRead[]): ToolsByPhase[] {
  return TOOL_PHASES.map((phase) => ({
    phase,
    tools: tools
      .filter((t) => t.task_kind === phase.key)
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name)),
  }));
}

// Fallback rendered on the Scope panel when a tool has no curated
// example_prompt. Deliberately generic — encourages tool authors to
// ship a real one but doesn't leave the button empty.
export function toolPromptOrFallback(tool: ToolRead): string {
  if (tool.example_prompt && tool.example_prompt.trim().length > 0) {
    return tool.example_prompt.trim();
  }
  return `Run ${tool.name} against an in-scope target.`;
}
