"use client";

// v2.20.0: IP enrichment inventory. Reads existing findings client-side;
// no new API endpoint.
// v2.21.0: adds Leaflet world map above the table (dynamic-imported so
// leaflet's window-touching module load never runs on the server).
// v2.22.0: also reads ipinfo findings — ASN / netblock / hosting-flag
// signal that freeipapi doesn't return. Merges by IP into a single row;
// geo columns prefer freeipapi, intel columns come from ipinfo.

import { useMemo } from "react";
import dynamic from "next/dynamic";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useFindings } from "@/lib/hooks";
import type { Finding } from "@/lib/types";
import type { MapPoint } from "@/components/leaflet-map";

const LeafletMap = dynamic(
  () => import("@/components/leaflet-map").then((m) => m.LeafletMap),
  {
    ssr: false,
    loading: () => (
      <div className="h-[360px] w-full animate-pulse rounded-lg bg-muted/40" />
    ),
  },
);

type DossierSource = "freeipapi" | "ipinfo";

interface DossierEntry {
  ip: string;
  countryName: string | null;
  countryCode: string | null;
  regionName: string | null;
  cityName: string | null;
  latitude: number | null;
  longitude: number | null;
  timeZone: string | null;
  hostname: string | null;
  asn: string | null;
  asnName: string | null;
  orgType: string | null;
  isProxy: boolean;
  isMobile: boolean;
  isVpn: boolean;
  isTor: boolean;
  isHosting: boolean;
  sources: Set<DossierSource>;
  observedAt: string | null;
}

function emptyEntry(ip: string): DossierEntry {
  return {
    ip,
    countryName: null,
    countryCode: null,
    regionName: null,
    cityName: null,
    latitude: null,
    longitude: null,
    timeZone: null,
    hostname: null,
    asn: null,
    asnName: null,
    orgType: null,
    isProxy: false,
    isMobile: false,
    isVpn: false,
    isTor: false,
    isHosting: false,
    sources: new Set<DossierSource>(),
    observedAt: null,
  };
}

function mergeFinding(map: Map<string, DossierEntry>, finding: Finding): void {
  const tool = finding.tool;
  if (tool !== "freeipapi" && tool !== "ipinfo") return;
  const data = finding.data as Record<string, unknown> | undefined;
  const items = data?.items;
  if (!Array.isArray(items) || items.length === 0) return;
  const item = items[0] as Record<string, unknown>;
  const ip = (item.ip as string | undefined) || finding.target || "";
  if (!ip) return;

  const entry = map.get(ip) ?? emptyEntry(ip);
  entry.sources.add(tool);

  // First-writer-wins for scalars (freeipapi runs first per the entity
  // action chain, so its geo lands first). If ipinfo runs solo we'll
  // still get the fields from its side.
  entry.countryName = entry.countryName ?? asStr(item.country_name);
  entry.countryCode = entry.countryCode ?? asStr(item.country_code);
  entry.regionName = entry.regionName ?? asStr(item.region_name);
  entry.cityName = entry.cityName ?? asStr(item.city_name);
  entry.timeZone = entry.timeZone ?? asStr(item.time_zone);
  entry.hostname = entry.hostname ?? asStr(item.hostname);

  const lat = toFiniteFloat(item.latitude);
  const lon = toFiniteFloat(item.longitude);
  if (entry.latitude === null && lat !== null) entry.latitude = lat;
  if (entry.longitude === null && lon !== null) entry.longitude = lon;

  entry.asn = entry.asn ?? asStr(item.asn);
  entry.asnName = entry.asnName ?? asStr(item.asn_name);
  entry.orgType = entry.orgType ?? asStr(item.org_type);

  // Flags — union across sources. Any-true wins.
  entry.isProxy = entry.isProxy || asBool(item.is_proxy);
  entry.isMobile = entry.isMobile || asBool(item.is_mobile);
  entry.isVpn = entry.isVpn || asBool(item.is_vpn);
  entry.isTor = entry.isTor || asBool(item.is_tor);
  entry.isHosting = entry.isHosting || asBool(item.is_hosting);

  const observed = finding.observed_at;
  if (observed && (!entry.observedAt || observed > entry.observedAt)) {
    entry.observedAt = observed;
  }

  map.set(ip, entry);
}

function asStr(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function asBool(value: unknown): boolean {
  return value === true;
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
  const parts = [
    entry.cityName,
    entry.regionName,
    entry.countryName ?? entry.countryCode,
  ].filter((value): value is string => Boolean(value));
  return parts.length > 0 ? parts.join(", ") : "—";
}

function formatAsn(entry: DossierEntry): string {
  if (entry.asn && entry.asnName) return `${entry.asn} · ${entry.asnName}`;
  return entry.asn ?? entry.asnName ?? "—";
}

function formatFlags(entry: DossierEntry): string {
  const parts = [
    entry.isProxy && "proxy",
    entry.isVpn && "vpn",
    entry.isTor && "tor",
    entry.isMobile && "mobile",
  ].filter((v): v is string => Boolean(v));
  return parts.length > 0 ? parts.join(" · ") : "—";
}

export function DossierView({ slug }: { slug: string }) {
  const { data: findings = [], error, isLoading } = useFindings(slug);

  const entries = useMemo(() => {
    const map = new Map<string, DossierEntry>();
    for (const f of findings) mergeFinding(map, f);
    return Array.from(map.values()).sort((a, b) =>
      a.ip.localeCompare(b.ip, undefined, { numeric: true }),
    );
  }, [findings]);

  const mapPoints = useMemo<MapPoint[]>(
    () =>
      entries
        .filter((e) => e.latitude !== null && e.longitude !== null)
        .map((e) => ({
          id: e.ip,
          lat: e.latitude as number,
          lon: e.longitude as number,
          label: `${e.ip} — ${formatLocation(e)}`,
        })),
    [entries],
  );

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-base font-medium">Dossier</h2>
        <p className="text-xs text-muted-foreground">
          IP intel inventory — geo (freeipapi) plus ASN, netblock owner, and
          hosting/VPN/proxy/Tor flags (ipinfo). One row per IP; each source
          contributes columns it knows. Run the tools from Scope, or use the
          quick actions on an IP entity.
        </p>
      </div>

      {error && (
        <p className="text-sm text-critical">
          {error instanceof Error ? error.message : String(error)}
        </p>
      )}

      {mapPoints.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">World map</CardTitle>
            <CardDescription>
              {mapPoints.length} IP{mapPoints.length === 1 ? "" : "s"} with
              coordinates. OpenStreetMap tiles.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <LeafletMap points={mapPoints} height={360} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Enriched IPs</CardTitle>
          <CardDescription>
            {isLoading
              ? "Loading findings…"
              : `${entries.length} IP${entries.length === 1 ? "" : "s"} — geo + intel merged across freeipapi and ipinfo.`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {entries.length === 0 && !isLoading && (
            <p className="text-sm text-muted-foreground">
              No IP enrichments yet. Upload keys at /settings/keys (providers{" "}
              <code className="font-mono">freeipapi</code> and{" "}
              <code className="font-mono">ipinfo</code>) and dispatch the tools
              from the Scope tab against an in-scope IP.
            </p>
          )}
          {entries.length > 0 && (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="px-3 py-2 w-40">IP</th>
                    <th className="px-3 py-2">Location</th>
                    <th className="px-3 py-2">ASN / Org</th>
                    <th className="px-3 py-2 w-24">Hosting</th>
                    <th className="px-3 py-2 w-32">Coords</th>
                    <th className="px-3 py-2 w-32">Flags</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((entry) => (
                    <tr
                      key={entry.ip}
                      className="border-b border-border/60 last:border-0"
                    >
                      <td className="px-3 py-2.5 font-mono text-xs">
                        <div>{entry.ip}</div>
                        {entry.hostname && (
                          <div className="text-[10px] text-muted-foreground">
                            {entry.hostname}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        <div>{formatLocation(entry)}</div>
                        {entry.timeZone && (
                          <div className="text-[11px] text-muted-foreground">
                            {entry.timeZone}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-xs">
                        {formatAsn(entry)}
                      </td>
                      <td className="px-3 py-2.5">
                        {entry.isHosting ? (
                          <Badge
                            variant="outline"
                            className="border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-200"
                          >
                            hosting
                          </Badge>
                        ) : entry.orgType ? (
                          <Badge variant="outline" className="text-muted-foreground">
                            {entry.orgType}
                          </Badge>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 font-mono text-[11px] text-muted-foreground">
                        {entry.latitude !== null && entry.longitude !== null
                          ? `${entry.latitude.toFixed(4)}, ${entry.longitude.toFixed(4)}`
                          : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-muted-foreground">
                        {formatFlags(entry)}
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
