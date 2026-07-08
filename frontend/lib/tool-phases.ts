// v1.11.0: shared taxonomy for grouping tools on the Settings > Tools
// banner and the Scope-tab "Current Tools" panel.
//
// v1.12.1: grouping switched from task-kind (enum/scan/exploit) to
// FindingPhase (osint/vuln_scan/…). The orchestrator ToolSpec's
// ``kind`` field is actually the target scope-kind (domain/ip/cidr),
// not the task-kind — the v1.12.0 wire projected it as-is and every
// tool fell through empty groups (see v1.12.0 → v1.12.1 hotfix). The
// correct grouping axis is ``phase_for_tool()`` on the backend, which
// returns FindingPhase strings.

import type { OrchestratorTool } from "@/lib/types";

// FindingPhase from backend/app/models/finding.py.
export type ToolPhase =
  | "osint"
  | "vuln_scan"
  | "exploit"
  | "phishing"
  | "general";

export interface ToolPhaseMeta {
  key: ToolPhase;
  label: string;
  hint: string;
}

export const TOOL_PHASES: ToolPhaseMeta[] = [
  {
    key: "osint",
    label: "OSINT",
    hint: "Passive recon — subfinder, dns, whois, crt.sh, and friends.",
  },
  {
    key: "vuln_scan",
    label: "Scanning",
    hint: "Active probes — port scans, subnet sweeps, service detection.",
  },
  {
    key: "exploit",
    label: "Analyst-only",
    hint: "Validation / proof-of-concept — analyst dispatches manually.",
  },
  {
    key: "phishing",
    label: "Phishing",
    hint: "Phishing workflows — analyst-driven.",
  },
  {
    key: "general",
    label: "Other",
    hint: "Uncategorized tools.",
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
      .filter((t) => t.phase === phase.key)
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name)),
  }));
}

// Fallback rendered on the Scope panel when a tool has no curated
// example_prompt. Orchestrator tools always ship an example_prompt
// (backend fills one for known tools + generic fallback for the
// rest), so this is mostly defensive.
export function toolPromptOrFallback(tool: OrchestratorTool): string {
  if (tool.example_prompt && tool.example_prompt.trim().length > 0) {
    return tool.example_prompt.trim();
  }
  return `Run ${tool.name} against an in-scope target.`;
}
