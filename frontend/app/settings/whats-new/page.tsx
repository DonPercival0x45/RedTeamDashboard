"use client";

import Link from "next/link";
import { useEffect } from "react";
import { ExternalLink } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ReleaseBody } from "@/components/release-body";
import { markVersionSeen } from "@/lib/release-notes";
import { useReleases } from "@/lib/hooks";

// Full history of releases as fetched by install.sh at deploy time and
// stamped into /releases.json. Visiting this page marks the latest
// version as seen so the banner doesn't keep reminding the analyst.

export default function SettingsWhatsNewPage() {
  // v1.0.0: shared useReleases cache. WhatsNewBanner reads the same key,
  // so this page's render is instant if the banner mounted first.
  const { data } = useReleases();
  const releases = data ?? null;

  useEffect(() => {
    if (releases && releases.length > 0) markVersionSeen(releases[0].tag_name);
  }, [releases]);

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
      <div>
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← engagements
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          What&apos;s new
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Release notes pulled from this deployment&apos;s GitHub Releases at
          install time. The newest version is highlighted; the banner stops
          nagging you about it once you&apos;ve visited this page.
        </p>
      </div>

      {releases === null && (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}

      {releases !== null && releases.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No release notes were stamped into this deployment. Run{" "}
          <code className="text-foreground">install.sh</code> again on a
          machine with GitHub API access to populate them.
        </p>
      )}

      {releases?.map((r, i) => (
        <Card key={r.tag_name} className={i === 0 ? "border-critical/40" : ""}>
          <CardHeader>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <CardTitle className="text-base">
                {r.name || r.tag_name}
              </CardTitle>
              <span className="text-xs text-muted-foreground">
                {new Date(r.published_at).toLocaleDateString()}
              </span>
            </div>
            <CardDescription className="flex items-center gap-1.5">
              <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
                {r.tag_name}
              </span>
              {i === 0 && (
                <span className="rounded-full border border-critical/40 bg-critical/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-critical">
                  Current
                </span>
              )}
              <a
                href={r.html_url}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                On GitHub <ExternalLink className="h-3 w-3" />
              </a>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ReleaseBody body={r.body} />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
