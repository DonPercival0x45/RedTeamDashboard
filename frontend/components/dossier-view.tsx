"use client";

// v2.20.0: IP enrichment inventory. Reads existing findings (filtered for
// tool === "freeipapi") client-side; no new API endpoint. Each finding's
// data.items[0] carries the parsed freeipapi response (country / city /
// lat / lon / ISP / timezone). Table-only in v2.20; the Leaflet map lands
// in v2.21 alongside the entity slideover thumbnail (deferred out of
// v2.20 because the npm install hit an IPv6-only registry endpoint on
// the analyst's network and we chose to ship the load-bearing enrichment
// data first).

import { useMemo } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useFindings } from "@/lib/hooks";
import type { Finding } from "@/lib/types";

interface DossierEntry {
  ip: string;
  countryName: string | null;
  regionName: string | null;
  cityName: string | null;
  latitude: number | null;
  longitude: number | null;
  timeZone: string | null;
  isProxy: boolean | null;
  isMobile: boolean | null;
  findingId: string;
  observedAt: string | null;
}

function extractDossierEntry(finding: Finding): DossierEntry | null {
  if (finding.tool !== "freeipapi") return null;
  const data = finding.data as Record<string, unknown> | undefined;
  const items = data?.items;
  if (!Array.isArray(items) || items.length === 0) return null;
  const item = items[0] as Record<string, unknown>;
  const ip = (item.ip as string | undefined) || finding.target || "";
  if (!ip) return null;
  const latitude = toFiniteFloat(item.latitude);
  const longitude = toFiniteFloat(item.longitude);
  return {
    ip,
    countryName: (item.country_name as string | null) ?? null,
    regionName: (item.region_name as string | null) ?? null,
    cityName: (item.city_name as string | null) ?? null,
    latitude,
    longitude,
    timeZone: (item.time_zone as string | null) ?? null,
    isProxy: (item.is_proxy as boolean | null) ?? null,
    isMobile: (item.is_mobile as boolean | null) ?? null,
    findingId: finding.id,
    observedAt: finding.observed_at,
  };
}

function toFiniteFloat(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatLocation(entry: DossierEntry): string {
  const parts = [entry.cityName, entry.regionName, entry.countryName].filter(
    (value): value is string => Boolean(value),
  );
  return parts.length > 0 ? parts.join(", ") : "—";
}

export function DossierView({ slug }: { slug: string }) {
  const { data: findings = [], error, isLoading } = useFindings(slug);

  const entries = useMemo(() => {
    const rows = findings
      .map(extractDossierEntry)
      .filter((entry): entry is DossierEntry => entry !== null);
    const seen = new Map<string, DossierEntry>();
    for (const row of rows) {
      if (!seen.has(row.ip)) seen.set(row.ip, row);
    }
    return Array.from(seen.values()).sort((a, b) =>
      a.ip.localeCompare(b.ip, undefined, { numeric: true }),
    );
  }, [findings]);

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-base font-medium">Dossier</h2>
        <p className="text-xs text-muted-foreground">
          IP enrichment inventory — country, region, city, ISP, and coordinates
          for every IP the freeipapi tool has touched in this engagement. Run
          the <code className="font-mono">freeipapi</code> tool from Scope to
          add an entry. World map lands in v2.21.
        </p>
      </div>

      {error && (
        <p className="text-sm text-critical">
          {error instanceof Error ? error.message : String(error)}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Enriched IPs</CardTitle>
          <CardDescription>
            {isLoading
              ? "Loading findings…"
              : `${entries.length} enriched IP${entries.length === 1 ? "" : "s"}.`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {entries.length === 0 && !isLoading && (
            <p className="text-sm text-muted-foreground">
              No IP enrichments yet. Upload a freeipapi API key at
              /settings/keys (provider=freeipapi) and dispatch the tool from
              the Scope tab against an in-scope IP.
            </p>
          )}
          {entries.length > 0 && (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="px-3 py-2 w-40">IP</th>
                    <th className="px-3 py-2">Location</th>
                    <th className="px-3 py-2 w-40">Timezone</th>
                    <th className="px-3 py-2 w-32">Coords</th>
                    <th className="px-3 py-2 w-24">Flags</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((entry) => (
                    <tr
                      key={entry.ip}
                      className="border-b border-border/60 last:border-0"
                    >
                      <td className="px-3 py-2.5 font-mono text-xs">
                        {entry.ip}
                      </td>
                      <td className="px-3 py-2.5">{formatLocation(entry)}</td>
                      <td className="px-3 py-2.5 text-muted-foreground">
                        {entry.timeZone || "—"}
                      </td>
                      <td className="px-3 py-2.5 font-mono text-[11px] text-muted-foreground">
                        {entry.latitude !== null && entry.longitude !== null
                          ? `${entry.latitude.toFixed(4)}, ${entry.longitude.toFixed(4)}`
                          : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-muted-foreground">
                        {[
                          entry.isProxy ? "proxy" : null,
                          entry.isMobile ? "mobile" : null,
                        ]
                          .filter(Boolean)
                          .join(" · ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
