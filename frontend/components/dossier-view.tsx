"use client";

// v2.20.0: IP enrichment inventory. Reads existing findings client-side;
// no new API endpoint.
// v2.21.0: adds Leaflet world map above the table (dynamic-imported so
// leaflet's window-touching module load never runs on the server).
// v2.22.0: also reads ipinfo findings — ASN / netblock / hosting-flag
// signal that freeipapi doesn't return. Merges by IP into a single row;
// geo columns prefer freeipapi, intel columns come from ipinfo.
// v2.24.0: adds a "Nearby wifi networks" card below the intel table,
// populated from wigle findings. Each wigle finding = one collapsible
// section per geo bucket; inner table lists SSID / BSSID / enc / channel.

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  BookOpenText,
  Box,
  Clock3,
  ExternalLink,
  FileSearch,
  GitBranch,
  Globe2,
  Search,
  ShieldCheck,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { EntityReviewDialog } from "@/components/entity-review-dialog";
import { EntitySlideOver } from "@/components/entities-view";
import { ConfirmDialog } from "@/components/confirm-dialog";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import {
  useEngagement,
  useEntities,
  useFindings,
  qk,
  useMe,
  useObservations,
  usePlaybookRuns,
  useScope,
  useStoredEntities,
} from "@/lib/hooks";
import type {
  Entity,
  Finding,
  Observation,
  PlaybookRunRead,
  ScopeItem,
  StoredEntity,
} from "@/lib/types";
import type { MapPoint } from "@/components/leaflet-map";
import { effectiveScopeState } from "@/lib/effective-scope";
import { deleteScopeItem, importScope } from "@/lib/api";
import { scopeTargetForEntity } from "@/lib/entity-scope";
import { engagementEntityHref } from "@/lib/engagement-links";
import {
  buildDossierTimeline,
  extractDossierRelationships,
  type DossierTimelineItem,
} from "@/lib/dossier";

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

const EMPTY_FINDINGS: Finding[] = [];
const EMPTY_ENTITIES: Entity[] = [];
const EMPTY_STORED_ENTITIES: StoredEntity[] = [];
const EMPTY_OBSERVATIONS: Observation[] = [];
const EMPTY_RUNS: PlaybookRunRead[] = [];

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

// v2.24.0: wigle wifi-network view. Each wigle finding becomes one section
// keyed by its geo bucket; the finding's data.items[] carries the per-BSSID
// records the analyst wants to browse.
interface WifiNetwork {
  bssid: string;
  ssid: string | null;
  encryption: string | null;
  channel: number | null;
  lastSeen: string | null;
  city: string | null;
  country: string | null;
}

interface WifiGroup {
  findingId: string;
  lat: number | null;
  lon: number | null;
  radiusKm: number | null;
  observedAt: string | null;
  networks: WifiNetwork[];
  nearestIp: string | null;
  nearestLocation: string | null;
}

function extractWifiGroups(
  findings: Finding[],
  enrichedIps: DossierEntry[],
): WifiGroup[] {
  const groups: WifiGroup[] = [];
  for (const f of findings) {
    if (f.tool !== "wigle") continue;
    const data = f.data as Record<string, unknown> | undefined;
    const items = data?.items;
    if (!Array.isArray(items) || items.length === 0) continue;
    const lat = toFiniteFloat(data?.lat);
    const lon = toFiniteFloat(data?.lon);
    const radiusKm = toFiniteFloat(data?.radius_km);
    const nearest = nearestEnrichedIp(lat, lon, enrichedIps);
    groups.push({
      findingId: f.id,
      lat,
      lon,
      radiusKm,
      observedAt: f.observed_at,
      nearestIp: nearest?.ip ?? null,
      nearestLocation: nearest ? formatLocation(nearest) : null,
      networks: items
        .map((it): WifiNetwork | null => {
          const item = it as Record<string, unknown>;
          const bssid = typeof item.bssid === "string" ? item.bssid.trim() : "";
          if (!bssid) return null;
          return {
            bssid,
            ssid: typeof item.ssid === "string" ? item.ssid : null,
            encryption:
              typeof item.encryption === "string" ? item.encryption : null,
            channel:
              typeof item.channel === "number" ? item.channel : null,
            lastSeen:
              typeof item.last_updated === "string" ? item.last_updated : null,
            city: typeof item.city === "string" ? item.city : null,
            country: typeof item.country === "string" ? item.country : null,
          };
        })
        .filter((n): n is WifiNetwork => n !== null),
    });
  }
  return groups.sort((a, b) => (b.observedAt ?? "").localeCompare(a.observedAt ?? ""));
}

function nearestEnrichedIp(
  lat: number | null,
  lon: number | null,
  entries: DossierEntry[],
): DossierEntry | null {
  if (lat === null || lon === null) return null;
  let best: { entry: DossierEntry; dist: number } | null = null;
  for (const e of entries) {
    if (e.latitude === null || e.longitude === null) continue;
    const dLat = e.latitude - lat;
    const dLon = e.longitude - lon;
    const dist = Math.sqrt(dLat * dLat + dLon * dLon);
    // ~0.5 degree ≈ 55km at equator — permissive so a rough geo still matches.
    if (dist < 0.5 && (!best || dist < best.dist)) best = { entry: e, dist };
  }
  return best?.entry ?? null;
}

const SEVERITY_STYLE: Record<string, string> = {
  critical: "border-red-500/50 bg-red-500/10 text-red-700 dark:text-red-200",
  high: "border-orange-500/50 bg-orange-500/10 text-orange-700 dark:text-orange-200",
  medium: "border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-200",
  low: "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-200",
  info: "text-muted-foreground",
};

const TIMELINE_STYLE: Record<
  DossierTimelineItem["trust"],
  { label: string; className: string }
> = {
  observed: {
    label: "Observed",
    className: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-200",
  },
  analyst: {
    label: "Analyst",
    className: "border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-200",
  },
  execution: {
    label: "Execution",
    className: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
  },
  record: {
    label: "Finding record",
    className: "border-border bg-muted/40 text-muted-foreground",
  },
};

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function relationshipVerb(kind: string): string {
  if (kind === "aliases_to") return "aliases to";
  if (kind === "delegates_to") return "delegates to";
  return "resolves to";
}

export function DossierView({ slug }: { slug: string }) {
  const qc = useQueryClient();
  const engagementQuery = useEngagement(slug);
  const findingsQuery = useFindings(slug);
  const entitiesQuery = useEntities(slug);
  const storedEntitiesQuery = useStoredEntities(slug);
  const scopeQuery = useScope(slug);
  const meQuery = useMe();
  const observationsQuery = useObservations(slug);
  const runsQuery = usePlaybookRuns(slug);
  const engagement = engagementQuery.data;
  const findings = findingsQuery.data ?? EMPTY_FINDINGS;
  const entities = entitiesQuery.data ?? EMPTY_ENTITIES;
  const storedEntities = storedEntitiesQuery.data ?? EMPTY_STORED_ENTITIES;
  const scopeItems = scopeQuery.data ?? [];
  const canWrite = meQuery.data !== undefined && meQuery.data.role !== "guest";
  const observations = observationsQuery.data ?? EMPTY_OBSERVATIONS;
  const runs = runsQuery.data ?? EMPTY_RUNS;
  const dossierQueries = [
    engagementQuery,
    findingsQuery,
    entitiesQuery,
    storedEntitiesQuery,
    observationsQuery,
    runsQuery,
  ];
  const dossierLoading = dossierQueries.some((query) => query.isLoading);
  const dossierError = dossierQueries.find((query) => query.error)?.error;
  const dossierReady = !dossierLoading && !dossierError;
  const [showAllTimeline, setShowAllTimeline] = useState(false);
  const [tab, setTab] = useState("overview");
  const [entityQuery, setEntityQuery] = useState("");
  const [entityFilter, setEntityFilter] = useState<"validation" | "all">("validation");
  const [relationshipQuery, setRelationshipQuery] = useState("");
  const [timelineQuery, setTimelineQuery] = useState("");
  const [selectedEntityKeys, setSelectedEntityKeys] = useState<Set<string>>(new Set());
  const [reviewAction, setReviewAction] = useState<"keep" | "exclude" | null>(null);
  const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null);
  const [scopeSaving, setScopeSaving] = useState(false);
  const [scopeMessage, setScopeMessage] = useState<string | null>(null);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const [pendingScopeRemoval, setPendingScopeRemoval] = useState<ScopeItem[]>([]);

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

  const wifiGroups = useMemo(
    () => extractWifiGroups(findings, entries),
    [findings, entries],
  );

  const relationships = useMemo(
    () => extractDossierRelationships(findings),
    [findings],
  );
  const timeline = useMemo(
    () => buildDossierTimeline(findings, observations, runs, relationships),
    [findings, observations, relationships, runs],
  );
  const findingStatusById = useMemo(
    () => new Map(findings.map((finding) => [finding.id, finding.status])),
    [findings],
  );
  const rankedEntities = useMemo(() => {
    const severityRank: Record<string, number> = {
      info: 0,
      low: 1,
      medium: 2,
      high: 3,
      critical: 4,
    };
    return entities
      .map((entity) => {
        const pendingFindingCount = entity.findings.filter((finding) => {
          const status = findingStatusById.get(finding.id);
          return status === "pending_validation" || status === "needs_review";
        }).length;
        const dispositionResolved =
          effectiveScopeState(entity) === "excluded" ||
          entity.review_disposition === "excluded" ||
          entity.review_disposition === "kept";
        const needsValidation =
          !dispositionResolved &&
          (pendingFindingCount > 0 ||
            entity.relevance === "review" ||
            entity.scope_status === "legacy");
        const validationPriority =
          pendingFindingCount * 100 +
          (entity.relevance === "review" ? 50 : 0) +
          (entity.scope_status === "legacy" ? 25 : 0) +
          (severityRank[entity.severity] ?? 0);
        return {
          entity,
          pendingFindingCount,
          needsValidation,
          validationPriority,
        };
      })
      .sort(
        (a, b) =>
          Number(b.needsValidation) - Number(a.needsValidation) ||
          b.validationPriority - a.validationPriority ||
          b.entity.count - a.entity.count ||
          a.entity.value.localeCompare(b.entity.value),
      );
  }, [entities, findingStatusById]);
  const reviewEntities = rankedEntities.filter((row) => row.needsValidation);
  const entityReviewTargets = useMemo(
    () =>
      rankedEntities
        .filter((row) =>
          selectedEntityKeys.has(`${row.entity.type}\u0000${row.entity.value}`),
        )
        .map((row) => ({ type: row.entity.type, value: row.entity.value })),
    [rankedEntities, selectedEntityKeys],
  );
  const normalizedEntityQuery = entityQuery.trim().toLowerCase();
  const visibleEntities = rankedEntities.filter(
    (row) =>
      (entityFilter === "all" || row.needsValidation) &&
      (!normalizedEntityQuery ||
        row.entity.value.toLowerCase().includes(normalizedEntityQuery) ||
        row.entity.type.toLowerCase().includes(normalizedEntityQuery)),
  );
  const normalizedRelationshipQuery = relationshipQuery.trim().toLowerCase();
  const visibleRelationships = relationships.filter(
    (relationship) =>
      !normalizedRelationshipQuery ||
      relationship.sourceValue.toLowerCase().includes(normalizedRelationshipQuery) ||
      relationship.targetValue.toLowerCase().includes(normalizedRelationshipQuery) ||
      relationship.findingTitle.toLowerCase().includes(normalizedRelationshipQuery),
  );
  const normalizedTimelineQuery = timelineQuery.trim().toLowerCase();
  const filteredTimeline = timeline.filter(
    (item) =>
      !normalizedTimelineQuery ||
      item.title.toLowerCase().includes(normalizedTimelineQuery) ||
      item.description.toLowerCase().includes(normalizedTimelineQuery) ||
      item.sourceLabel.toLowerCase().includes(normalizedTimelineQuery),
  );
  const visibleTimeline = showAllTimeline
    ? filteredTimeline
    : filteredTimeline.slice(0, 12);
  const enrichedIps = useMemo(
    () => new Set(entries.map((entry) => entry.ip)),
    [entries],
  );
  const missingIpContext = entities.filter(
    (entity) => entity.type === "ip" && !enrichedIps.has(entity.value),
  );
  const pendingFindings = findings.filter(
    (finding) =>
      finding.status === "pending_validation" || finding.status === "needs_review",
  );
  const incompleteRuns = runs.filter(
    (run) => run.status === "failed" || run.status === "partial",
  );
  const validatedFindings = findings.filter(
    (finding) => finding.status === "validated",
  ).length;
  const currentSelectedEntity = selectedEntity
    ? entities.find(
        (entity) =>
          entity.type === selectedEntity.type && entity.value === selectedEntity.value,
      ) ?? selectedEntity
    : null;

  const refreshEntityScope = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: qk.scope(slug) }),
      qc.invalidateQueries({ queryKey: qk.entities(slug) }),
      qc.invalidateQueries({ queryKey: qk.storedEntities(slug) }),
      qc.invalidateQueries({ queryKey: qk.engagements() }),
    ]);
  };

  const assignSelectedEntityScope = async (disposition: "include" | "exclude") => {
    if (!selectedEntity || !canWrite || scopeSaving) return;
    const target = scopeTargetForEntity(selectedEntity);
    if (!target) return;
    setScopeSaving(true);
    setScopeMessage(null);
    setScopeError(null);
    try {
      const result = await importScope(
        slug,
        `${disposition === "exclude" ? "!" : ""}${target.value}`,
        "found",
      );
      if (result.errors.length > 0) {
        throw new Error("This entity could not be converted into an exact scope rule.");
      }
      setScopeMessage(
        disposition === "include" ? "Entity added to scope." : "Entity excluded from scope.",
      );
    } catch (error) {
      setScopeError(error instanceof Error ? error.message : String(error));
    } finally {
      await refreshEntityScope();
      setScopeSaving(false);
    }
  };

  const removeSelectedScopeRules = async () => {
    if (!canWrite || pendingScopeRemoval.length === 0 || scopeSaving) return;
    setScopeSaving(true);
    setScopeMessage(null);
    setScopeError(null);
    try {
      await Promise.all(
        pendingScopeRemoval.map((item) => deleteScopeItem(slug, item.id)),
      );
      setScopeMessage(
        `${pendingScopeRemoval.length} exact scope ${pendingScopeRemoval.length === 1 ? "rule" : "rules"} removed.`,
      );
      setPendingScopeRemoval([]);
    } catch (error) {
      setScopeError(error instanceof Error ? error.message : String(error));
    } finally {
      await refreshEntityScope();
      setScopeSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <BookOpenText className="h-5 w-5 text-primary" aria-hidden="true" />
            <h2 className="text-lg font-semibold">Engagement dossier</h2>
          </div>
          <p className="max-w-3xl text-sm text-muted-foreground">
            The evidence-backed story of {engagement?.name ?? slug}: what was
            observed, how the pieces connect, and which questions still need an
            analyst. Open any citation to inspect the underlying entity,
            finding, or execution.
          </p>
        </div>
        <div className="flex gap-2">
          <Badge variant="outline" className="gap-1 text-muted-foreground">
            <ShieldCheck className="h-3 w-3" aria-hidden="true" />
            Evidence-backed
          </Badge>
          <Badge variant="outline" className="gap-1 text-muted-foreground">
            <Clock3 className="h-3 w-3" aria-hidden="true" />
            Live projection
          </Badge>
        </div>
      </div>

      {dossierLoading && (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground" role="status">
            Assembling the engagement narrative and provenance…
          </CardContent>
        </Card>
      )}

      {dossierError && (
        <Card className="border-critical/50">
          <CardContent className="p-6" role="alert">
            <p className="font-medium text-critical">The dossier is incomplete.</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Narrative counts and gap conclusions are hidden until every
              required evidence source can be loaded. {dossierError instanceof Error ? dossierError.message : String(dossierError)}
            </p>
          </CardContent>
        </Card>
      )}

      {dossierReady && (
        <Tabs value={tab} onValueChange={setTab} className="space-y-4">
          <div className="border-b border-border bg-background py-3 lg:sticky lg:top-0 lg:z-10">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {[
          { label: "Observed entities", value: entities.length, Icon: Box },
          { label: "Needs validation", value: reviewEntities.length, Icon: AlertTriangle },
          { label: "Findings", value: findings.length, Icon: FileSearch },
          { label: "Evidence paths", value: relationships.length, Icon: GitBranch },
          { label: "Playbook runs", value: runs.length, Icon: Search },
        ].map(({ label, value, Icon }) => (
          <Card key={label}>
            <CardContent className="flex items-center justify-between p-4">
              <div>
                <p className="text-2xl font-semibold tabular-nums">{value}</p>
                <p className="text-xs text-muted-foreground">{label}</p>
              </div>
              <Icon className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
            </CardContent>
          </Card>
        ))}
      </div>
            <TabsList className="mt-3 border-b-0">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="relationships">Relationships</TabsTrigger>
              <TabsTrigger value="entities" className="gap-1.5">
                Entity review
                {reviewEntities.length > 0 && (
                  <Badge className="h-5 min-w-5 justify-center px-1.5 text-[10px]" variant="destructive">
                    {reviewEntities.length}
                  </Badge>
                )}
              </TabsTrigger>
              <TabsTrigger value="timeline">Timeline</TabsTrigger>
              <TabsTrigger value="infrastructure">Infrastructure</TabsTrigger>
              <TabsTrigger value="research">Research gaps</TabsTrigger>
            </TabsList>
          </div>

      <TabsContent value="overview" className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <BookOpenText className="h-4 w-4" aria-hidden="true" />
            Current picture
          </CardTitle>
          <CardDescription>
            Deterministic summary of the records currently in the engagement.
            It does not change scope or promote an observation into a finding.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm leading-6">
          {engagement?.description && <p>{engagement.description}</p>}
          <p>
            The dashboard currently correlates <strong>{entities.length}</strong>{" "}
            entities across <strong>{findings.length}</strong> findings. Of those
            findings, <strong>{validatedFindings}</strong> are validated and{" "}
            <strong>{pendingFindings.length}</strong> still require review.
          </p>
          {relationships.length > 0 && (
            <p>
              Structured DNS evidence supplies <strong>{relationships.length}</strong>{" "}
              explainable relationship {relationships.length === 1 ? "path" : "paths"}.
              These paths describe what a tool observed; they do not establish
              ownership or authorization.
            </p>
          )}
          {storedEntities.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {storedEntities.length} persisted/imported entities are retained
              alongside the live finding-derived inventory.
            </p>
          )}
        </CardContent>
      </Card>
      </TabsContent>

      <TabsContent value="relationships" className="space-y-4">
      {relationships.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <GitBranch className="h-4 w-4" aria-hidden="true" />
              How the infrastructure connects
            </CardTitle>
            <CardDescription>
              Relationships extracted only from structured DNS evidence. Every
              path links back to the finding that recorded it.
            </CardDescription>
            <div className="relative max-w-md">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <Input
                type="search"
                value={relationshipQuery}
                onChange={(event) => setRelationshipQuery(event.target.value)}
                aria-label="Search infrastructure relationships"
                placeholder="Find a host, IP, or source finding"
                className="pl-8"
              />
            </div>
          </CardHeader>
          <CardContent className="grid max-h-[60vh] gap-2 overflow-y-auto lg:grid-cols-2">
            {visibleRelationships.map((relationship) => (
              <div
                key={relationship.id}
                className="rounded-lg border border-border bg-muted/15 p-3"
              >
                <div className="flex min-w-0 items-center gap-2 text-sm">
                  <Link
                    href={engagementEntityHref(slug, {
                      type: relationship.sourceType,
                      value: relationship.sourceValue,
                    })}
                    className="truncate font-mono text-xs hover:underline"
                  >
                    {relationship.sourceValue}
                  </Link>
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    {relationshipVerb(relationship.kind)}
                  </span>
                  <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <Link
                    href={engagementEntityHref(slug, {
                      type: relationship.targetType,
                      value: relationship.targetValue,
                    })}
                    className="truncate font-mono text-xs font-medium hover:underline"
                  >
                    {relationship.targetValue}
                  </Link>
                </div>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
                  <span>{formatDateTime(relationship.observedAt)}</span>
                  <Link
                    href={`/e/findings/${relationship.findingId}?slug=${encodeURIComponent(slug)}`}
                    className="inline-flex items-center gap-1 hover:text-foreground hover:underline"
                  >
                    Evidence
                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                  </Link>
                </div>
              </div>
            ))}
            {visibleRelationships.length === 0 && (
              <p className="text-sm text-muted-foreground">No relationships match that search.</p>
            )}
          </CardContent>
        </Card>
      )}
      </TabsContent>

      <TabsContent value="entities" className="space-y-4">
      {rankedEntities.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <AlertTriangle className="h-4 w-4" aria-hidden="true" />
              Entity validation queue
            </CardTitle>
            <CardDescription>
              Entities requiring analyst judgment are always ordered first.
              Relevance labels are advisory and never authorize a target.
            </CardDescription>
            <div className="flex flex-wrap gap-2">
              <div className="relative min-w-60 flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
                <Input
                  type="search"
                  value={entityQuery}
                  onChange={(event) => setEntityQuery(event.target.value)}
                  aria-label="Search entity validation queue"
                  placeholder="Find an entity by value or type"
                  className="pl-8"
                />
              </div>
              <div className="flex overflow-hidden rounded-md border border-border">
                <button
                  type="button"
                  aria-pressed={entityFilter === "validation"}
                  onClick={() => setEntityFilter("validation")}
                  className={entityFilter === "validation" ? "bg-muted px-3 py-2 text-xs font-medium" : "px-3 py-2 text-xs text-muted-foreground"}
                >
                  Needs validation ({reviewEntities.length})
                </button>
                <button
                  type="button"
                  aria-pressed={entityFilter === "all"}
                  onClick={() => setEntityFilter("all")}
                  className={entityFilter === "all" ? "border-l border-border bg-muted px-3 py-2 text-xs font-medium" : "border-l border-border px-3 py-2 text-xs text-muted-foreground"}
                >
                  All ({rankedEntities.length})
                </button>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-muted/20 px-3 py-2">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <button
                  type="button"
                  onClick={() =>
                    setSelectedEntityKeys(
                      new Set(
                        visibleEntities.map(
                          (row) => `${row.entity.type}\u0000${row.entity.value}`,
                        ),
                      ),
                    )
                  }
                  className="rounded border border-border px-2 py-1 hover:text-foreground"
                >
                  Select all matching ({visibleEntities.length})
                </button>
                {selectedEntityKeys.size > 0 && (
                  <button
                    type="button"
                    onClick={() => setSelectedEntityKeys(new Set())}
                    className="hover:text-foreground hover:underline"
                  >
                    Clear
                  </button>
                )}
                <span>{entityReviewTargets.length} selected</span>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={entityReviewTargets.length === 0}
                  onClick={() => setReviewAction("keep")}
                  className="rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
                >
                  Keep reviewed
                </button>
                <button
                  type="button"
                  disabled={entityReviewTargets.length === 0}
                  onClick={() => setReviewAction("exclude")}
                  className="rounded-md border border-amber-500/50 px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-500/10 disabled:opacity-50 dark:text-amber-200"
                >
                  Exclude…
                </button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="grid max-h-[60vh] gap-2 overflow-y-auto md:grid-cols-2 xl:grid-cols-3">
            {visibleEntities.map(({ entity, pendingFindingCount, needsValidation }) => {
              const selectionKey = `${entity.type}\u0000${entity.value}`;
              return (
              <div
                key={`${entity.type}:${entity.value}`}
                className="relative rounded-lg border border-border transition-colors hover:bg-muted/35"
              >
                <input
                  type="checkbox"
                  aria-label={`Select ${entity.type} ${entity.value}`}
                  checked={selectedEntityKeys.has(selectionKey)}
                  onChange={(event) => {
                    const next = new Set(selectedEntityKeys);
                    if (event.target.checked) next.add(selectionKey);
                    else next.delete(selectionKey);
                    setSelectedEntityKeys(next);
                  }}
                  className="absolute left-3 top-3 z-[1]"
                />
                <button
                  type="button"
                  aria-haspopup="dialog"
                  aria-label={`Open ${entity.type} ${entity.value}`}
                  onClick={() => {
                    setScopeMessage(null);
                    setScopeError(null);
                    setSelectedEntity(entity);
                  }}
                  className="block w-full p-3 pl-9 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-xs font-medium">{entity.value}</p>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      {entity.type} · {entity.count} finding{entity.count === 1 ? "" : "s"}
                    </p>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    {needsValidation && (
                      <Badge className="bg-amber-500/15 text-amber-800 dark:text-amber-200" variant="outline">
                        Needs validation
                      </Badge>
                    )}
                    <Badge
                      variant="outline"
                      className={SEVERITY_STYLE[entity.severity] ?? "text-muted-foreground"}
                    >
                      {entity.severity}
                    </Badge>
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  <Badge variant="outline" className="text-[10px] text-muted-foreground">
                    {entity.scope_status === "live" ? "in scope" : entity.scope_status}
                  </Badge>
                  {entity.review_disposition && (
                    <Badge variant="outline" className="text-[10px] text-muted-foreground">
                      reviewed · {entity.review_disposition}
                    </Badge>
                  )}
                  {entity.relevance && (
                    <Badge variant="outline" className="text-[10px] text-muted-foreground">
                      {entity.relevance.replaceAll("_", " ")}
                    </Badge>
                  )}
                </div>
                {needsValidation && (
                  <p className="mt-2 text-[11px] text-amber-700 dark:text-amber-300">
                    {pendingFindingCount > 0
                      ? `${pendingFindingCount} linked finding${pendingFindingCount === 1 ? "" : "s"} awaiting validation`
                      : entity.scope_status === "legacy"
                        ? "Previously scoped entity needs disposition review"
                        : "Relevance to the engagement needs analyst review"}
                  </p>
                )}
                <p className="mt-2 text-[10px] text-muted-foreground">
                  First seen {formatDateTime(entity.first_seen)} · Last seen {formatDateTime(entity.last_seen)}
                </p>
                </button>
              </div>
              );
            })}
            {visibleEntities.length === 0 && (
              <p className="text-sm text-muted-foreground">
                {entityFilter === "validation"
                  ? "No entities currently require validation."
                  : "No entities match that search."}
              </p>
            )}
          </CardContent>
        </Card>
      )}
      </TabsContent>

      <TabsContent value="timeline" className="space-y-4">
      {timeline.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Clock3 className="h-4 w-4" aria-hidden="true" />
              Engagement timeline
            </CardTitle>
            <CardDescription>
              Observations, analyst notes, findings, and playbook outcomes in
              one chronology. Labels distinguish evidence from interpretation.
            </CardDescription>
            <div className="relative max-w-md">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <Input
                type="search"
                value={timelineQuery}
                onChange={(event) => setTimelineQuery(event.target.value)}
                aria-label="Search dossier timeline"
                placeholder="Find an event, target, tool, or run"
                className="pl-8"
              />
            </div>
          </CardHeader>
          <CardContent>
            <ol className="relative ml-2 border-l border-border">
              {visibleTimeline.map((item) => {
                const style = TIMELINE_STYLE[item.trust];
                const href = item.findingId
                  ? `/e/findings/${item.findingId}?slug=${encodeURIComponent(slug)}`
                  : item.runId
                    ? `/e?slug=${encodeURIComponent(slug)}&view=status&run=${encodeURIComponent(item.runId)}`
                    : item.entityType && item.entityValue
                      ? engagementEntityHref(slug, {
                          type: item.entityType as Entity["type"],
                          value: item.entityValue,
                        })
                      : null;
                return (
                  <li key={item.id} className="relative pb-5 pl-6 last:pb-0">
                    <span className="absolute -left-1.5 top-1.5 h-3 w-3 rounded-full border-2 border-background bg-muted-foreground" />
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline" className={style.className}>
                        {style.label}
                      </Badge>
                      <time className="text-[11px] text-muted-foreground">
                        {formatDateTime(item.occurredAt)}
                      </time>
                    </div>
                    <p className="mt-1 text-sm font-medium">{item.title}</p>
                    <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                      {item.description}
                    </p>
                    <div className="mt-1.5 flex items-center gap-3 text-[11px] text-muted-foreground">
                      <span>{item.sourceLabel}</span>
                      {href && (
                        <Link href={href} className="inline-flex items-center gap-1 hover:text-foreground hover:underline">
                          Open source
                          <ExternalLink className="h-3 w-3" aria-hidden="true" />
                        </Link>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
            {filteredTimeline.length === 0 && (
              <p className="text-sm text-muted-foreground">No timeline events match that search.</p>
            )}
            {filteredTimeline.length > 12 && (
              <button
                type="button"
                onClick={() => setShowAllTimeline((value) => !value)}
                className="mt-4 rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {showAllTimeline ? "Show recent activity" : `Show all ${filteredTimeline.length} events`}
              </button>
            )}
          </CardContent>
        </Card>
      )}
      </TabsContent>

      <TabsContent value="research" className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            Research gaps
          </CardTitle>
          <CardDescription>
            Missing context and incomplete work are shown as questions—not as
            findings or authorization decisions.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {missingIpContext.length > 0 && (
            <p><strong>{missingIpContext.length}</strong> observed IPs do not yet have ownership or routing enrichment.</p>
          )}
          {reviewEntities.length > 0 && (
            <p><strong>{reviewEntities.length}</strong> entities remain in the relevance review queue.</p>
          )}
          {pendingFindings.length > 0 && (
            <p><strong>{pendingFindings.length}</strong> findings still require analyst validation.</p>
          )}
          {incompleteRuns.length > 0 && (
            <p><strong>{incompleteRuns.length}</strong> playbook runs ended partial or failed and may have incomplete context.</p>
          )}
          {missingIpContext.length === 0 &&
            reviewEntities.length === 0 &&
            pendingFindings.length === 0 &&
            incompleteRuns.length === 0 && (
              <p className="text-muted-foreground">No immediate dossier gaps were detected.</p>
            )}
        </CardContent>
      </Card>
      </TabsContent>

      <TabsContent value="infrastructure" className="space-y-4">
      {mapPoints.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Globe2 className="h-4 w-4" aria-hidden="true" />
              Infrastructure globe
            </CardTitle>
            <CardDescription>
              Geographic context for {mapPoints.length} enriched IP{mapPoints.length === 1 ? "" : "s"}.
              Location is supporting context, not proof of ownership or impact.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <LeafletMap points={mapPoints} height={360} />
          </CardContent>
        </Card>
      )}

      {!findingsQuery.error && (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Enriched IPs</CardTitle>
          <CardDescription>
            {findingsQuery.isLoading
              ? "Loading findings…"
              : `${entries.length} IP${entries.length === 1 ? "" : "s"} — geo + intel merged across freeipapi and ipinfo.`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {entries.length === 0 && !findingsQuery.isLoading && (
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
      )}

      {wifiGroups.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Nearby wifi networks</CardTitle>
            <CardDescription>
              WiGLE.net lookups near enriched IPs. Each section is one geo
              query — expand to see BSSIDs, SSIDs, encryption, and channel.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {wifiGroups.map((g) => (
              <details
                key={g.findingId}
                className="rounded-lg border border-border"
                open={wifiGroups.length === 1}
              >
                <summary className="cursor-pointer list-none px-3 py-2 text-sm hover:bg-muted/40">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-medium">
                      {g.nearestIp
                        ? `Near ${g.nearestIp}`
                        : g.lat !== null && g.lon !== null
                          ? `Near ${g.lat.toFixed(4)}, ${g.lon.toFixed(4)}`
                          : "Near unknown coord"}
                    </span>
                    <Badge variant="outline" className="text-muted-foreground">
                      {g.networks.length} network{g.networks.length === 1 ? "" : "s"}
                    </Badge>
                    {g.nearestLocation && (
                      <span className="text-xs text-muted-foreground">
                        {g.nearestLocation}
                      </span>
                    )}
                    {g.radiusKm !== null && (
                      <span className="text-[11px] text-muted-foreground">
                        · radius {g.radiusKm.toFixed(2)} km
                      </span>
                    )}
                  </div>
                </summary>
                <div className="overflow-x-auto border-t border-border">
                  <table className="w-full border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                        <th className="px-3 py-2">SSID</th>
                        <th className="px-3 py-2 w-44">BSSID</th>
                        <th className="px-3 py-2 w-24">Enc</th>
                        <th className="px-3 py-2 w-16">Ch</th>
                        <th className="px-3 py-2 w-32">Last seen</th>
                      </tr>
                    </thead>
                    <tbody>
                      {g.networks.map((n) => (
                        <tr
                          key={n.bssid}
                          className="border-b border-border/60 last:border-0"
                        >
                          <td className="px-3 py-2">
                            {n.ssid || <span className="text-muted-foreground">—</span>}
                          </td>
                          <td className="px-3 py-2 font-mono text-[11px] text-muted-foreground">
                            {n.bssid}
                          </td>
                          <td className="px-3 py-2 text-xs">{n.encryption || "—"}</td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            {n.channel ?? "—"}
                          </td>
                          <td className="px-3 py-2 text-[11px] text-muted-foreground">
                            {n.lastSeen || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            ))}
          </CardContent>
        </Card>
      )}
      </TabsContent>
        </Tabs>
      )}
      {currentSelectedEntity ? (
        <EntitySlideOver
          entity={currentSelectedEntity}
          slug={slug}
          onClose={() => setSelectedEntity(null)}
          canWrite={canWrite}
          scopeItems={scopeItems}
          scopeSaving={scopeSaving}
          scopeMessage={scopeMessage}
          scopeError={scopeError}
          onAssignScope={(disposition) => void assignSelectedEntityScope(disposition)}
          onRemoveScopeRules={setPendingScopeRemoval}
          doneTools={
            new Set(
              currentSelectedEntity.findings
                .map((finding) => finding.tool)
                .filter((tool): tool is string => Boolean(tool)),
            )
          }
        />
      ) : null}
      <ConfirmDialog
        open={pendingScopeRemoval.length > 0}
        title={`Remove exact scope ${pendingScopeRemoval.length === 1 ? "rule" : "rules"}?`}
        description="Removing an exact rule changes future authorization but preserves all findings and evidence."
        confirmLabel="Remove rule"
        busy={scopeSaving}
        onConfirm={() => void removeSelectedScopeRules()}
        onOpenChange={(open) => !open && !scopeSaving && setPendingScopeRemoval([])}
      />
      {reviewAction && (
        <EntityReviewDialog
          slug={slug}
          targets={entityReviewTargets}
          action={reviewAction}
          open
          onOpenChange={(open) => {
            if (!open) setReviewAction(null);
          }}
          onApplied={() => setSelectedEntityKeys(new Set())}
        />
      )}
    </div>
  );
}
