// v2.4.0 — derived "pending" state for engagements. UI-only; the
// backend still stores `status = active` for these rows. An engagement
// counts as pending when its setup isn't complete yet:
//
//   - fewer than one in-scope item defined, OR
//   - no `state = current` strategy revision, OR
//   - the analyst-set start_date is still in the future (engagement
//     hasn't reached the window it's meant to run in).
//
// Anything that doesn't match any of the above and is `active` renders
// as active. Archived / flushed engagements never render as pending —
// their terminal status wins.

import type { Engagement } from "@/lib/types";

export function isPendingEngagement(eng: Engagement): boolean {
  if (eng.status !== "active") return false;
  const scopeCount = eng.scope_count ?? 0;
  if (scopeCount < 1) return true;
  if (!eng.has_strategy) return true;
  if (eng.start_date && isFutureDate(eng.start_date)) return true;
  return false;
}

// Reason label for the pending badge / card subtitle. Returns the first
// missing prerequisite in the same priority order isPendingEngagement
// checks. Callers can show a small "needs: scope" style hint on the
// pending card.
export function pendingReason(eng: Engagement): "scope" | "strategy" | "not-started" | null {
  if (!isPendingEngagement(eng)) return null;
  const scopeCount = eng.scope_count ?? 0;
  if (scopeCount < 1) return "scope";
  if (!eng.has_strategy) return "strategy";
  if (eng.start_date && isFutureDate(eng.start_date)) return "not-started";
  return null;
}

// Compare against today at 00:00 in the viewer's local tz. `start_date`
// is a plain YYYY-MM-DD (no tz), which the analyst set as the day work
// begins — so "today or earlier" is treated as already started.
function isFutureDate(isoDate: string): boolean {
  const start = new Date(`${isoDate}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return start.getTime() > today.getTime();
}
