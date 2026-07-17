"use client";

// v2.10.1 — subtle 2px indeterminate progress bar pinned to the top of
// the app-shell whenever any TanStack query is in flight OR any mutation
// is pending. Auto-refetches (VM table's 15s poll, useEngagements etc.)
// count too — so we gate visibility on a 250ms show-delay to suppress
// the flash on fast round-trips. If the query finishes before 250ms
// have elapsed, the bar never renders.

import { useEffect, useState } from "react";
import { useIsFetching, useIsMutating } from "@tanstack/react-query";

const SHOW_DELAY_MS = 250;

export function GlobalFetchingBar() {
  const fetching = useIsFetching();
  const mutating = useIsMutating();
  const anyInFlight = fetching > 0 || mutating > 0;
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!anyInFlight) {
      setVisible(false);
      return;
    }
    const t = window.setTimeout(() => setVisible(true), SHOW_DELAY_MS);
    return () => window.clearTimeout(t);
  }, [anyInFlight]);

  if (!visible) return null;
  return (
    <div
      role="progressbar"
      aria-busy="true"
      aria-label="Loading"
      className="pointer-events-none fixed inset-x-0 top-0 z-50 h-[2px] overflow-hidden bg-transparent"
    >
      <div className="rtd-progress-bar h-full w-1/3 bg-critical" />
    </div>
  );
}
