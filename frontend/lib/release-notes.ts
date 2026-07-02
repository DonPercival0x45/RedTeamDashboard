// What's-New surface — loads categorized releases from the backend
// (v1.3.1+). The backend fetches from GitHub Releases API + runs the
// categorization enricher + caches in-memory for ~1h, so the frontend
// gets fresh data without needing to rebuild the Docker image on every
// release. Tracks per-browser "last seen version" in localStorage so
// the banner only shows when something new has actually landed since
// the analyst's last visit.

import { API_BASE_URL } from "@/lib/config";
import type { ReleaseNote } from "@/lib/types";

const STORAGE_KEY = "rtd.whats-new.last-seen-version.v1";

let cached: ReleaseNote[] | null = null;
let cachedPromise: Promise<ReleaseNote[]> | null = null;

export async function loadReleases(): Promise<ReleaseNote[]> {
  if (cached !== null) return cached;
  if (cachedPromise) return cachedPromise;
  // v1.3.1: was fetch("/releases.json") — the file was baked into the
  // frontend Docker image at CI build time (empty in the repo, so the
  // Container App served []). Now fetched live from the backend.
  cachedPromise = fetch(`${API_BASE_URL}/releases.json`, { cache: "no-cache" })
    .then(async (res) => {
      if (!res.ok) return [];
      const body = (await res.json()) as ReleaseNote[];
      // Newest first — GitHub already returns that order, but defend
      // against an operator hand-edit landing them in reverse.
      body.sort(
        (a, b) =>
          new Date(b.published_at).getTime() -
          new Date(a.published_at).getTime(),
      );
      cached = body;
      return body;
    })
    .catch(() => {
      // Missing file, bad JSON, offline — degrade silently. The banner
      // simply won't render.
      cached = [];
      return cached;
    });
  return cachedPromise;
}

export function currentVersion(releases: ReleaseNote[]): string | null {
  return releases.length > 0 ? releases[0].tag_name : null;
}

export function getLastSeenVersion(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function markVersionSeen(tag: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, tag);
  } catch {
    // Storage disabled / quota — banner re-shows next session. Not fatal.
  }
}

export function hasUnseenRelease(
  releases: ReleaseNote[],
  lastSeen: string | null,
): boolean {
  const current = currentVersion(releases);
  if (!current) return false;
  // First-ever visit (no localStorage yet): suppress the banner so a brand
  // new analyst doesn't get hit with the full history. They can read it
  // anytime from /settings/whats-new.
  if (lastSeen === null) return false;
  return lastSeen !== current;
}
