"use client";

// v3 Convergence C6e — dismissable-per-session banner nudging analysts to
// convert legacy engagements to v3. Renders across every engagement tab as
// long as ``intelligence_architecture === "legacy"``. The convert action
// itself lives on the Strategy tab (existing UI); this just deep-links there.

import { ArrowRight, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import type { Engagement } from "@/lib/types";

const DISMISS_KEY_PREFIX = "rtd:legacy-banner-dismissed:";

export function LegacyEngagementBanner({
  engagement,
  onOpenStrategy,
}: {
  engagement: Engagement;
  onOpenStrategy: () => void;
}) {
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const key = `${DISMISS_KEY_PREFIX}${engagement.slug}`;
    setDismissed(window.sessionStorage.getItem(key) === "1");
  }, [engagement.slug]);

  if (engagement.intelligence_architecture !== "legacy" || dismissed) {
    return null;
  }

  const dismiss = () => {
    if (typeof window !== "undefined") {
      window.sessionStorage.setItem(
        `${DISMISS_KEY_PREFIX}${engagement.slug}`,
        "1",
      );
    }
    setDismissed(true);
  };

  return (
    <div className="flex items-start gap-3 rounded-lg border border-violet-500/40 bg-violet-500/5 p-4">
      <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-violet-500" />
      <div className="flex-1 space-y-1">
        <p className="text-sm font-medium">
          This engagement is still on the legacy intelligence pipeline.
        </p>
        <p className="text-xs text-muted-foreground">
          v3 replaces the per-finding strategist with a methodology-driven
          coverage plan and analyst-triggered playbook runs. Conversion is
          one-way but preserves scope, findings, and history.
        </p>
      </div>
      <div className="flex items-center gap-1">
        <Button size="sm" variant="outline" onClick={onOpenStrategy}>
          Convert to v3
          <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
        </Button>
        <Button
          size="icon"
          variant="ghost"
          onClick={dismiss}
          aria-label="Dismiss banner for this session"
          className="h-8 w-8"
          title="Dismiss for this session (comes back on next login)"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}
