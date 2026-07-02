"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import {
  currentVersion,
  getLastSeenVersion,
  hasUnseenRelease,
  markVersionSeen,
} from "@/lib/release-notes";
import { useAuth } from "@/lib/auth";
import { useReleases } from "@/lib/hooks";

// Top-of-page strip that appears once per browser-session-per-new-version.
// "Seen" state lives in localStorage keyed to the latest tag in
// /releases.json. Dismissing or clicking through to /settings/whats-new
// marks the current version as seen so the banner stays hidden until the
// next deploy.
export function WhatsNewBanner() {
  const { identity } = useAuth();
  // v1.0.0: shared useReleases cache. The settings/whats-new page reads
  // the same query key, so its render is instant after the banner mounts.
  const { data: releases } = useReleases();
  const [dismissed, setDismissed] = useState(false);

  const latestTag = releases ? currentVersion(releases) : null;
  const latestName = releases?.[0]?.name ?? latestTag;
  const visible = Boolean(
    identity &&
      !dismissed &&
      latestTag &&
      releases &&
      hasUnseenRelease(releases, getLastSeenVersion()),
  );

  const dismiss = useCallback(() => {
    if (latestTag) markVersionSeen(latestTag);
    setDismissed(true);
  }, [latestTag]);

  if (!visible || !latestTag) return null;

  return (
    <div className="border-b border-critical/40 bg-critical/5">
      <div className="container flex flex-wrap items-center gap-3 py-2 text-xs">
        <span className="rounded-full border border-critical/40 bg-critical/10 px-2 py-0.5 font-semibold uppercase tracking-wide text-critical">
          New
        </span>
        <span className="text-foreground">
          <span className="font-semibold">{latestTag}</span>
          {latestName && latestName !== latestTag ? ` — ${latestName}` : ""} is
          live.
        </span>
        <span className="ml-auto flex items-center gap-3">
          <Link
            href="/settings/whats-new"
            onClick={dismiss}
            className="text-foreground underline decoration-dotted hover:decoration-solid"
          >
            View what changed
          </Link>
          <button
            type="button"
            onClick={dismiss}
            aria-label="Dismiss what's new banner"
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </span>
      </div>
    </div>
  );
}
