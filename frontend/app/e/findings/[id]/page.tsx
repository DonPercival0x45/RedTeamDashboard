// v0.21.0: Finding "pane of glass" — a full-page detail view per finding
// (the lightweight slide-over stays for quick edits). Phase 1 renders the
// finding header + the activity timeline (Tasks / agent runs / audit
// events) and reserves a right rail for the AI chatbot (Phase 2).
//
// Route: /e/findings/[id]?slug=<engagement-slug>

import { Suspense } from "react";
import { FindingPaneWithSlug } from "./finding-pane-client";

export default async function FindingPanePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <Suspense
      fallback={
        <p className="px-6 py-10 text-sm text-muted-foreground">Loading…</p>
      }
    >
      <FindingPaneWithSlug id={id} />
    </Suspense>
  );
}
