// Derived workspace grouping. The backend stores active engagements as active
// regardless of planning dates or whether an analyst has authored strategy.
// Keep "pending" as a setup cue only for engagements without usable scope;
// it is not an execution lifecycle state.

import type { Engagement } from "@/lib/types";

export function isPendingEngagement(eng: Engagement): boolean {
  return eng.status === "active" && (eng.scope_count ?? 0) < 1;
}

export function pendingReason(eng: Engagement): "scope" | null {
  return isPendingEngagement(eng) ? "scope" : null;
}
