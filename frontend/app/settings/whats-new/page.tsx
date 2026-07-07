"use client";

import Link from "next/link";
import { useEffect } from "react";
import {
  ExternalLink,
  Rocket,
  Bug,
  Sparkles,
  Wrench,
} from "lucide-react";
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
import type {
  ReleaseCategoryEntry,
  ReleaseCategories,
} from "@/lib/types";

// v1.3.0 What's New Cleanup — bucket categories rendered above the
// fold, install boilerplate (Deploy/CLI/Images) folded into a
// collapsed <details>. install.sh stamps the categories in at deploy
// time via /compare-with-previous-tag; releases predating that stamp
// fall through to the raw-body path (no categories field).

const CATEGORY_META: {
  key: keyof ReleaseCategories;
  label: string;
  icon: typeof Sparkles;
  tone: string;
}[] = [
  {
    key: "features",
    label: "Features",
    icon: Sparkles,
    tone: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
  },
  {
    key: "fixes",
    label: "Bug Fixes",
    icon: Bug,
    tone: "border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-200",
  },
  {
    key: "qol",
    label: "Quality of Life",
    icon: Rocket,
    tone: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-200",
  },
  {
    key: "ops",
    label: "Operations",
    icon: Wrench,
    tone: "border-slate-500/40 bg-slate-500/10 text-slate-200",
  },
];

function CategoryBlock({
  label,
  icon: Icon,
  tone,
  entries,
  repoUrl,
}: {
  label: string;
  icon: typeof Sparkles;
  tone: string;
  entries: ReleaseCategoryEntry[];
  repoUrl: string;
}) {
  if (entries.length === 0) return null;
  return (
    <section className="space-y-1.5">
      <h3
        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${tone}`}
      >
        <Icon className="h-3 w-3" />
        {label}
      </h3>
      <ul className="ml-4 list-disc space-y-1 text-sm text-muted-foreground">
        {entries.map((e) => (
          <li key={e.sha}>
            <span className="text-foreground">{e.title}</span>
            {e.pr !== null && (
              <>
                {" "}
                <a
                  href={`${repoUrl}/pull/${e.pr}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-muted-foreground underline decoration-dotted hover:decoration-solid"
                >
                  #{e.pr}
                </a>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

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

      {releases?.map((r, i) => {
        // Derive the repo URL for PR links without hard-coding an owner.
        // html_url is https://github.com/<owner>/<repo>/releases/tag/<tag>
        // — trim from ``/releases/`` onwards to get the repo root.
        const repoUrl = r.html_url.split("/releases/")[0];
        const categories = r.categories ?? null;
        const totalCategorized = categories
          ? CATEGORY_META.reduce(
              (n, m) => n + categories[m.key].length,
              0,
            )
          : 0;
        return (
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
            <CardContent className="space-y-4">
              {categories && totalCategorized > 0 ? (
                <>
                  {CATEGORY_META.map((m) => (
                    <CategoryBlock
                      key={m.key}
                      label={m.label}
                      icon={m.icon}
                      tone={m.tone}
                      entries={categories[m.key]}
                      repoUrl={repoUrl}
                    />
                  ))}
                  {/* v1.3.0: install boilerplate folded into a collapsed
                      toggle so it doesn't drown the changelog. Ops-y
                      analysts who need the pip/docker snippets click
                      through. */}
                  <details className="rounded-md border border-border bg-secondary/30">
                    <summary className="cursor-pointer select-none px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                      Install details (deploy · CLI · images)
                    </summary>
                    <div className="border-t border-border p-3">
                      <ReleaseBody body={r.body} />
                    </div>
                  </details>
                </>
              ) : categories ? (
                // Enriched but no user-facing entries — probably the
                // first-ever release (no prev tag to compare against).
                // Fall through to raw-body render with install
                // sections stripped, so the card isn't empty.
                <>
                  <ReleaseBody body={r.body} hideInstallSections />
                  <details className="rounded-md border border-border bg-secondary/30">
                    <summary className="cursor-pointer select-none px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                      Install details (deploy · CLI · images)
                    </summary>
                    <div className="border-t border-border p-3">
                      <ReleaseBody body={r.body} />
                    </div>
                  </details>
                </>
              ) : (
                // Legacy releases.json (no categories field) — render
                // the raw body unchanged. Older deploys stay working.
                <ReleaseBody body={r.body} />
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
