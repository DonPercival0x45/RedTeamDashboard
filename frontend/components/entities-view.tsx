"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  Check,
  Layers,
  ListPlus,
  Loader2,
  RotateCcw,
  Search,
  Trash2,
  Upload,
  Zap,
} from "lucide-react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { QueryState } from "@/components/query-state";
import type { MapPoint } from "@/components/leaflet-map";

// v2.21.0: thumbnail map for IP-type entities. Dynamic-imported (ssr:false)
// so leaflet's window-touching module load never runs on the server.
const LeafletMap = dynamic(
  () => import("@/components/leaflet-map").then((m) => m.LeafletMap),
  {
    ssr: false,
    loading: () => (
      <div className="h-[180px] w-full animate-pulse rounded-lg bg-muted/40" />
    ),
  },
);
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  createEntityGroup,
  dissolveEntityGroup,
  deleteScopeItem,
  importEntitiesDarkweb,
  importEntitiesMaltego,
  importScope,
  mergeDeleteEntityGroup,
  restoreStoredEntity,
  suppressStoredEntity,
} from "@/lib/api";
import {
  qk,
  useEntities,
  useEntityDuplicateCandidates,
  useFindings,
  useScope,
  useStoredEntities,
} from "@/lib/hooks";
import {
  entityKey,
  scopeActionState,
  scopeTargetForEntity,
} from "@/lib/entity-scope";
import { effectiveScopeState } from "@/lib/effective-scope";
import { cn } from "@/lib/utils";
import type {
  DarkwebImportResult,
  Entity,
  EntityDuplicateCandidate,
  MaltegoImportResult,
  ScopeItem,
  Severity,
  StoredEntity,
} from "@/lib/types";

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
  info: 0,
};

const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "border-critical/50 bg-critical/15 text-critical",
  high: "border-zinc-500/40 text-zinc-800 dark:text-zinc-100",
  medium: "border-zinc-600/40 text-zinc-600 dark:text-zinc-300",
  low: "border-zinc-700/40 text-zinc-700 dark:text-zinc-400",
  info: "border-zinc-800 text-zinc-700 dark:text-zinc-500",
};

const TYPE_LABEL: Record<string, string> = {
  email: "Email",
  ip: "IP",
  cidr: "CIDR",
  domain: "Domain",
  subdomain: "Subdomain",
  url: "URL",
  host: "Host",
};

// v1.4.13: roadmap #10 -- a first-move prompt per entity type. Returns
// {label, prompt}; missing types fall back to no quick-action button.
// v1.4.14: roadmap #8 -- engagement-aware quick actions. Each entity
// type maps to an ORDERED recon chain; the slide-over highlights the
// next un-run step as primary and dims steps already completed against
// this entity (detected from findings with matching target + tool).
// ``tool`` is the source_tool name to match against finding.tool; null
// means "not a tool run" (e.g. email investigation) so it's never dimmed.
type EntityAction = {
  tool: string | null;
  label: string;
  prompt: (value: string) => string;
};

const ENTITY_ACTION_CHAINS: Record<string, EntityAction[]> = {
  domain: [
    {
      tool: "subfinder",
      label: "Enumerate subdomains",
      prompt: (v) =>
        `Enumerate subdomains, DNS records, and certificate-transparency logs for ${v}, then probe what's live.`,
    },
    {
      tool: "portscan",
      label: "Port-scan discovered hosts",
      prompt: (v) =>
        `Run port discovery against the hosts discovered under ${v}, then enumerate open services.`,
    },
    {
      tool: "service_detect",
      label: "Service-detect open ports",
      prompt: (v) =>
        `Service-detect and fingerprint the open ports discovered under ${v}.`,
    },
  ],
  subdomain: [
    {
      tool: "subfinder",
      label: "Enumerate subdomains",
      prompt: (v) =>
        `Enumerate subdomains, DNS records, and certificate-transparency logs for ${v}, then probe what's live.`,
    },
    {
      tool: "portscan",
      label: "Port-scan host",
      prompt: (v) =>
        `Run port discovery and service detection against ${v}, then enumerate any open services.`,
    },
  ],
  host: [
    {
      tool: "subfinder",
      label: "Enumerate subdomains",
      prompt: (v) =>
        `Enumerate subdomains, DNS records, and certificate-transparency logs for ${v}, then probe what's live.`,
    },
    {
      tool: "portscan",
      label: "Port-scan host",
      prompt: (v) =>
        `Run port discovery and service detection against ${v}, then enumerate any open services.`,
    },
  ],
  ip: [
    {
      tool: "freeipapi",
      label: "IP Enrichment",
      prompt: (v) =>
        `Enrich ${v} via freeipapi to look up country, region, city, ISP, coordinates, and proxy/mobile flags.`,
    },
    {
      tool: "ipinfo",
      label: "ASN + Hosting Intel",
      prompt: (v) =>
        `Look up ${v} via ipinfo to get ASN, netblock owner, org type, and hosting/VPN/proxy/Tor flags. Complements freeipapi's geo with netblock and infra signal.`,
    },
    {
      tool: "wigle",
      label: "Wifi networks nearby",
      prompt: (v) =>
        `Look up wifi networks near ${v}'s geographic location using wigle. If ${v} hasn't been enriched yet, first run freeipapi or ipinfo on it to obtain lat/lon, then dispatch wigle with those coordinates and a small radius (default 0.5 km).`,
    },
    {
      tool: "portscan",
      label: "Port-scan IP",
      prompt: (v) =>
        `Run port discovery and service detection against ${v}, then enumerate any open services.`,
    },
    {
      tool: "service_detect",
      label: "Service-detect open ports",
      prompt: (v) =>
        `Service-detect and fingerprint the open ports on ${v}.`,
    },
  ],
  cidr: [
    {
      tool: "subnet_sweep",
      label: "Sweep CIDR for live hosts",
      prompt: (v) =>
        `Discover live hosts in ${v} and enumerate open ports and services across the range.`,
    },
    {
      tool: "portscan",
      label: "Port-scan live hosts",
      prompt: (v) =>
        `Port-scan the live hosts discovered in ${v} and enumerate open services.`,
    },
  ],
  url: [
    {
      tool: "httpx_probe",
      label: "Probe URL",
      prompt: (v) =>
        `Recon and probe ${v}: fingerprint the stack, enumerate paths, and surface anything notable.`,
    },
  ],
  email: [
    {
      tool: null,
      label: "Investigate email",
      prompt: (v) =>
        `Investigate ${v}: pivot on the email for associated accounts, breaches, and exposed credentials.`,
    },
  ],
};

function typeLabel(t: string): string {
  return TYPE_LABEL[t] ?? t;
}

// Scope-status badge. Live = matches current scope; Legacy = matched a scope
// item that was later removed; OOS = outside the current authorization set.
// The table wraps this badge in a button that opens explicit scope controls.
function ScopeStatusBadge({ status }: { status: string }) {
  const label =
    status === "live"
      ? "Live"
      : status === "excluded"
        ? "Excluded"
        : status === "legacy"
          ? "Legacy"
          : "Out of scope";
  const className =
    status === "live"
      ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200"
      : status === "excluded"
        ? "border-rose-500/50 bg-rose-500/10 text-rose-700 dark:text-rose-200"
        : status === "legacy"
          ? "border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-200"
          : "border-border text-muted-foreground";
  return (
    <Badge variant="outline" className={className}>
      {label}
    </Badge>
  );
}

// CHARTER Idea 4: entities correlated across the engagement's findings —
// searchable, filterable by type, clickable into provenance. Phase 10 adds
// an "Imported" section above the derived list for entities that landed
// from external sources (Maltego today, future Dehashed etc.).
export function EntitiesView({
  slug,
  canWrite,
  onQuickAction,
  onLaunchPlaybook,
}: {
  slug: string;
  canWrite: boolean;
  onQuickAction?: (prompt: string) => void;
  onLaunchPlaybook?: (target: { type: string; value: string }) => void;
}) {
  // v1.0.0: react-query owns the derived-entities fetch. Focus revalidation
  // catches new findings that landed while the tab was hidden.
  const qc = useQueryClient();
  const params = useSearchParams();
  const entitiesQuery = useEntities(slug);
  const scopeQuery = useScope(slug);
  const entities = entitiesQuery.data;
  const scopeItems = scopeQuery.data ?? [];
  const { error } = entitiesQuery;
  const [search, setSearch] = useState("");
  const [type, setType] = useState<string>("all");
  // Actionable, authorized entities are the default. Historical, excluded,
  // and unmatched inventory stays available behind an explicit review action.
  const [scopeStatus, setScopeStatus] = useState<
    "all" | "live" | "excluded" | "legacy" | "oos"
  >("live");
  const [showScopeOutliers, setShowScopeOutliers] = useState(false);
  const [hideLikelyThirdParty, setHideLikelyThirdParty] = useState(true);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [scopeSaving, setScopeSaving] = useState(false);
  const [scopeMessage, setScopeMessage] = useState<string | null>(null);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const [pendingScopeRemoval, setPendingScopeRemoval] = useState<ScopeItem[]>([]);
  const detailTriggerRef = useRef<HTMLButtonElement | null>(null);
  const deepLinkAppliedRef = useRef(false);
  const deepLinkType = params.get("type");
  const deepLinkValue = params.get("value");

  useEffect(() => {
    if (deepLinkAppliedRef.current || !entities || !deepLinkType || !deepLinkValue) return;
    const match = entities.find(
      (entity) => entity.type === deepLinkType && entity.value === deepLinkValue,
    );
    deepLinkAppliedRef.current = true;
    if (!match) return;
    setHideLikelyThirdParty(false);
    setShowScopeOutliers(true);
    setType("all");
    setScopeStatus("all");
    setSearch(deepLinkValue);
    setSelectedKey(entityKey(match));
  }, [deepLinkType, deepLinkValue, entities]);

  if (entities === undefined)
    return (
      <div className="space-y-5">
        <ImportedEntitiesSection slug={slug} canWrite={canWrite} />
        <QueryState
          isLoading={entitiesQuery.isLoading}
          error={error}
          hasData={false}
          loadingLabel="Loading scope and discovered entities…"
          errorLabel="Could not load scope and discovered entities."
          onRetry={() => void entitiesQuery.refetch()}
          isRetrying={entitiesQuery.isFetching}
        />
      </div>
    );

  const types = ["all", ...Array.from(new Set(entities.map((e) => e.type)))];
  const q = search.trim().toLowerCase();
  const likelyThirdPartyCount = entities.filter(
    (entity) => entity.relevance === "likely_third_party",
  ).length;
  const matchesScopeStatus = (entity: Entity) => {
    if (scopeStatus === "all") return true;
    const projectedState = effectiveScopeState(entity);
    if (scopeStatus === "live") return projectedState === "included";
    if (scopeStatus === "excluded") return projectedState === "excluded";
    if (projectedState === "included" || projectedState === "excluded") return false;
    return scopeStatus === "legacy"
      ? entity.scope_status === "legacy"
      : entity.scope_status !== "legacy";
  };
  const visible = entities
    .filter(
      (entity) =>
        !hideLikelyThirdParty || entity.relevance !== "likely_third_party",
    )
    .filter((e) => type === "all" || e.type === type)
    .filter(matchesScopeStatus)
    .filter((e) => !q || e.value.toLowerCase().includes(q))
    .sort(
      (a, b) =>
        SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] ||
        b.count - a.count,
    );

  const selected = selectedKey
    ? entities.find((entity) => entityKey(entity) === selectedKey) ?? null
    : null;
  const selectableVisible = visible.filter((entity) => scopeTargetForEntity(entity));
  const selectedEntities = entities.filter(
    (entity) =>
      selectedKeys.has(entityKey(entity)) &&
      (!hideLikelyThirdParty || entity.relevance !== "likely_third_party"),
  );
  const includeCandidates = selectedEntities.filter(
    (entity) => scopeActionState(entity, scopeItems).canAdd,
  );
  const exclusionCandidates = selectedEntities.filter(
    (entity) => scopeActionState(entity, scopeItems).canExclude,
  );
  const allVisibleSelected =
    selectableVisible.length > 0 &&
    selectableVisible.every((entity) => selectedKeys.has(entityKey(entity)));

  const refreshScopeViews = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: qk.scope(slug) }),
      qc.invalidateQueries({ queryKey: qk.entities(slug) }),
      qc.invalidateQueries({ queryKey: qk.storedEntities(slug) }),
      qc.invalidateQueries({ queryKey: qk.engagements() }),
    ]);
  };

  const assignScope = async (
    targets: Entity[],
    disposition: "include" | "exclude",
  ) => {
    const assignable = targets.filter((entity) => scopeTargetForEntity(entity));
    if (!canWrite || assignable.length === 0 || scopeSaving) return;
    setScopeSaving(true);
    setScopeError(null);
    setScopeMessage(null);
    try {
      const text = assignable
        .map((entity) => `${disposition === "exclude" ? "!" : ""}${entity.value}`)
        .join("\n");
      const result = await importScope(slug, text, "found");
      if (result.errors.length > 0) {
        throw new Error(
          `${result.errors.length} selected ${result.errors.length === 1 ? "entity was" : "entities were"} not valid scope targets.`,
        );
      }

      const assignedKeys = new Set(assignable.map(entityKey));
      setSelectedKeys((previous) => {
        const next = new Set(previous);
        assignedKeys.forEach((key) => next.delete(key));
        return next;
      });
      setScopeMessage(
        disposition === "include"
          ? `${assignable.length} ${assignable.length === 1 ? "entity" : "entities"} added to scope.`
          : `${assignable.length} ${assignable.length === 1 ? "entity" : "entities"} excluded from scope.`,
      );
    } catch (err) {
      setScopeError(err instanceof Error ? err.message : String(err));
    } finally {
      await refreshScopeViews();
      setScopeSaving(false);
    }
  };

  const removeScopeRules = async (items: ScopeItem[]) => {
    if (!canWrite || items.length === 0 || scopeSaving) return;
    setScopeSaving(true);
    setScopeError(null);
    setScopeMessage(null);
    try {
      await Promise.all(items.map((item) => deleteScopeItem(slug, item.id)));
      setScopeMessage(
        `${items.length} exact scope ${items.length === 1 ? "rule" : "rules"} removed.`,
      );
      setPendingScopeRemoval([]);
    } catch (err) {
      setScopeError(err instanceof Error ? err.message : String(err));
    } finally {
      await refreshScopeViews();
      setScopeSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <ImportedEntitiesSection slug={slug} canWrite={canWrite} />

      {error ? (
        <QueryState
          isLoading={false}
          error={error}
          hasData
          errorLabel="Could not refresh scope and discovered entities."
          onRetry={() => void entitiesQuery.refetch()}
          isRetrying={entitiesQuery.isFetching}
          compact
        />
      ) : null}

      <div className="space-y-1">
        <h2 className="text-base font-medium">Scope and discoveries</h2>
        <p className="text-xs text-muted-foreground">
          Declared targets appear immediately; playbook and tool evidence adds
          discovered infrastructure and provenance.
        </p>
        {likelyThirdPartyCount > 0 ? (
          <div className="flex flex-wrap items-center gap-2 pt-2 text-xs">
            <Badge variant="outline">
              {likelyThirdPartyCount} likely third-party
            </Badge>
            <button
              type="button"
              className="text-muted-foreground underline underline-offset-2 hover:text-foreground"
              onClick={() => setHideLikelyThirdParty((current) => !current)}
            >
              {hideLikelyThirdParty
                ? "Show collapsed vendor contacts"
                : "Hide likely vendor contacts"}
            </button>
            <span className="text-muted-foreground">
              Advisory only — nothing is deleted or removed from evidence.
            </span>
          </div>
        ) : null}
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search entities by value…"
          className="pl-9"
        />
      </div>

      <div className="flex flex-wrap gap-1">
        {types.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setType(t)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-xs transition-colors",
              type === t
                ? "border-critical/50 bg-critical/10 text-foreground"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {t === "all" ? "All types" : typeLabel(t)}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-1">
        <button
          type="button"
          onClick={() => setScopeStatus("live")}
          className={cn(
            "rounded-full border px-2.5 py-1 text-xs transition-colors",
            scopeStatus === "live"
              ? "border-primary/50 bg-primary/10 text-foreground"
              : "border-border text-muted-foreground hover:text-foreground",
          )}
        >
          Live
        </button>
        <button
          type="button"
          aria-expanded={showScopeOutliers}
          onClick={() => {
            if (showScopeOutliers) setScopeStatus("live");
            setShowScopeOutliers(!showScopeOutliers);
          }}
          className="rounded-full border border-dashed border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          {showScopeOutliers ? "Show live only" : "Review other scope states"}
        </button>
        {showScopeOutliers &&
          (["all", "excluded", "legacy", "oos"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setScopeStatus(s)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs transition-colors",
                scopeStatus === s
                  ? "border-primary/50 bg-primary/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {s === "all"
                ? "All scope"
                : s === "excluded"
                  ? "Excluded"
                  : s === "legacy"
                    ? "Legacy"
                    : "Out of scope"}
            </button>
          ))}
      </div>

      {canWrite && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card/40 p-3">
          <span className="mr-auto text-xs text-muted-foreground" aria-live="polite">
            {selectedEntities.length > 0
              ? `${selectedEntities.length} selected`
              : "Select entities to update scope in one action."}
          </span>
          {selectedEntities.length > 0 && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setSelectedKeys(new Set())}
              disabled={scopeSaving}
            >
              Clear
            </Button>
          )}
          <Button
            type="button"
            size="sm"
            onClick={() => void assignScope(includeCandidates, "include")}
            disabled={includeCandidates.length === 0 || scopeSaving}
            title={
              selectedEntities.length > 0 && includeCandidates.length === 0
                ? "Every selected entity is already in scope or has an exact rule to resolve first."
                : undefined
            }
          >
            {scopeSaving ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <ListPlus className="mr-1.5 h-3.5 w-3.5" />
            )}
            {includeCandidates.length > 0
              ? `Add ${includeCandidates.length} to scope`
              : "Add to scope"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void assignScope(exclusionCandidates, "exclude")}
            disabled={exclusionCandidates.length === 0 || scopeSaving}
            title={
              selectedEntities.length > 0 && exclusionCandidates.length === 0
                ? "Every selected entity already has an exact scope rule to resolve first."
                : undefined
            }
          >
            <Ban className="mr-1.5 h-3.5 w-3.5" />
            {exclusionCandidates.length > 0
              ? `Exclude ${exclusionCandidates.length}`
              : "Exclude"}
          </Button>
        </div>
      )}

      {scopeMessage && !selected && (
        <p className="text-sm text-emerald-700 dark:text-emerald-300" role="status">
          {scopeMessage}
        </p>
      )}
      {scopeError && !selected && (
        <p className="text-sm text-critical" role="alert">
          {scopeError}
        </p>
      )}

      {visible.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {entities.length
            ? "No entities match these filters."
            : "No entities yet — add scope or run a collection playbook."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                {canWrite && (
                  <th className="w-10 px-3 py-2">
                    <input
                      type="checkbox"
                      aria-label="Select all visible scope-compatible entities"
                      checked={allVisibleSelected}
                      ref={(node) => {
                        if (node) {
                          node.indeterminate =
                            !allVisibleSelected &&
                            selectableVisible.some((entity) =>
                              selectedKeys.has(entityKey(entity)),
                            );
                        }
                      }}
                      disabled={selectableVisible.length === 0 || scopeSaving}
                      onChange={(event) => {
                        setSelectedKeys((previous) => {
                          const next = new Set(previous);
                          selectableVisible.forEach((entity) => {
                            const key = entityKey(entity);
                            if (event.target.checked) next.add(key);
                            else next.delete(key);
                          });
                          return next;
                        });
                      }}
                    />
                  </th>
                )}
                <th className="px-3 py-2 w-28">Type</th>
                <th className="px-3 py-2">Value</th>
                <th className="px-3 py-2 w-24">Scope</th>
                <th className="px-3 py-2 w-20">Findings</th>
                <th className="px-3 py-2 w-24">Severity</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((e) => (
                <tr
                  key={`${e.type}:${e.value}`}
                  className="border-b border-border/60 last:border-0 hover:bg-secondary/40"
                >
                  {canWrite && (
                    <td className="px-3 py-2.5">
                      <input
                        type="checkbox"
                        aria-label={`Select ${e.value}`}
                        checked={selectedKeys.has(entityKey(e))}
                        disabled={!scopeTargetForEntity(e) || scopeSaving}
                        title={
                          scopeTargetForEntity(e)
                            ? "Select entity"
                            : `${typeLabel(e.type)} entities cannot be scope targets`
                        }
                        onChange={(event) => {
                          setSelectedKeys((previous) => {
                            const next = new Set(previous);
                            if (event.target.checked) next.add(entityKey(e));
                            else next.delete(entityKey(e));
                            return next;
                          });
                        }}
                      />
                    </td>
                  )}
                  <td className="px-3 py-2.5">
                    <span className="text-xs text-muted-foreground">
                      {typeLabel(e.type)}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs">
                    <button
                      type="button"
                      aria-haspopup="dialog"
                      className="rounded-sm text-left hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      onClick={(event) => {
                        detailTriggerRef.current = event.currentTarget;
                        setSelectedKey(entityKey(e));
                      }}
                    >
                      {e.value}
                    </button>
                  </td>
                  <td className="px-3 py-2.5">
                    <button
                      type="button"
                      aria-label={`Manage scope for ${e.value}`}
                      className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      onClick={(event) => {
                        detailTriggerRef.current = event.currentTarget;
                        setSelectedKey(entityKey(e));
                      }}
                    >
                      <ScopeStatusBadge status={e.scope_status} />
                    </button>
                    {e.relevance === "likely_third_party" ? (
                      <Badge
                        variant="outline"
                        className="ml-1 border-violet-500/40 text-violet-700 dark:text-violet-300"
                        title={e.relevance_reason ?? undefined}
                      >
                        Likely vendor
                      </Badge>
                    ) : null}
                  </td>
                  <td className="px-3 py-2.5 tabular-nums text-muted-foreground">
                    {e.count}
                  </td>
                  <td className="px-3 py-2.5">
                    <Badge variant="outline" className={SEVERITY_CLASS[e.severity]}>
                      {e.severity}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <EntitySlideOver
          entity={selected}
          slug={slug}
          onClose={() => {
            setSelectedKey(null);
            requestAnimationFrame(() => detailTriggerRef.current?.focus());
          }}
          onQuickAction={onQuickAction}
          onLaunchPlaybook={onLaunchPlaybook}
          canWrite={canWrite}
          scopeItems={scopeItems}
          scopeSaving={scopeSaving}
          scopeMessage={scopeMessage}
          scopeError={scopeError}
          onAssignScope={(disposition) => void assignScope([selected], disposition)}
          onRemoveScopeRules={setPendingScopeRemoval}
          doneTools={
            new Set(
              selected.findings
                .map((finding) => finding.tool)
                .filter((tool): tool is string => Boolean(tool)),
            )
          }
        />
      )}

      <ConfirmDialog
        open={pendingScopeRemoval.length > 0}
        title={`Remove exact scope ${pendingScopeRemoval.length === 1 ? "rule" : "rules"}?`}
        description={
          pendingScopeRemoval.some((item) => item.is_exclusion) ? (
            <>
              Removing an exclusion may authorize this target through a broader
              scope rule. The entity and its evidence will remain available.
            </>
          ) : (
            <>
              This target will no longer be eligible for new playbook runs unless
              another scope rule still includes it. The entity and its evidence
              will remain available.
            </>
          )
        }
        confirmLabel="Remove rule"
        busy={scopeSaving}
        onConfirm={() => removeScopeRules(pendingScopeRemoval)}
        onOpenChange={(open) => !open && setPendingScopeRemoval([])}
      />
    </div>
  );
}

// ───── Imported entities section (Maltego today, future Dehashed etc.) ─────

// Last-import receipt; one shape per source so the UI doesn't have to
// unify schemas. The renderer below pattern-matches on the kind tag.
type LastImport =
  | { kind: "maltego"; result: MaltegoImportResult }
  | { kind: "darkweb"; result: DarkwebImportResult };

function ImportedEntitiesSection({
  slug,
  canWrite,
}: {
  slug: string;
  canWrite: boolean;
}) {
  // v1.0.0: react-query owns the stored-entities fetch. Import mutations
  // patch the cache directly via qc.setQueryData.
  const qc = useQueryClient();
  const [showRemoved, setShowRemoved] = useState(false);
  const { data: items, error: queryError } = useStoredEntities(slug, showRemoved);
  const { data: duplicateCandidates = [] } = useEntityDuplicateCandidates(slug);
  const [canonicalChoices, setCanonicalChoices] = useState<Record<string, string>>({});
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [managing, setManaging] = useState<string | null>(null);
  const loadError =
    queryError instanceof Error
      ? queryError.message
      : queryError
        ? String(queryError)
        : null;

  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [lastImport, setLastImport] = useState<LastImport | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshManagement = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["stored-entities", slug] }),
      qc.invalidateQueries({ queryKey: qk.entityDuplicateCandidates(slug) }),
    ]);
  };

  const groupCandidate = async (candidate: EntityDuplicateCandidate) => {
    const reason = window.prompt(
      "Why should these stored entities be grouped? All records and provenance will be retained.",
      "Equivalent representations of the same entity",
    );
    if (!reason?.trim()) return;
    setManaging(`candidate:${candidate.type}:${candidate.normalized_value}`);
    setUploadError(null);
    try {
      await createEntityGroup(slug, {
        entity_ids: candidate.entities.map((item) => item.id),
        canonical_entity_id:
          canonicalChoices[`${candidate.type}:${candidate.normalized_value}`] ??
          candidate.suggested_canonical_entity_id,
        reason: reason.trim(),
      });
      await refreshManagement();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setManaging(null);
    }
  };

  const disposeEntity = async (entity: StoredEntity) => {
    const restoring = entity.suppressed;
    const reason = window.prompt(
      restoring
        ? "Why are you restoring this stored entity?"
        : "Remove this stored entity from active views? The record, properties, links, and audit history will be retained.",
    );
    if (!reason?.trim()) return;
    setManaging(entity.id);
    setUploadError(null);
    try {
      if (restoring) await restoreStoredEntity(entity, reason.trim());
      else await suppressStoredEntity(entity, reason.trim());
      await refreshManagement();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setManaging(null);
    }
  };

  const mergeDeleteGroup = async (entity: StoredEntity) => {
    if (!entity.group) return;
    const reason = window.prompt(
      "Copy missing provenance to the canonical entity and remove duplicates from active views. The identity group and all original records, properties, and links remain stored; removed members can be restored from Show removed. Why is this safe?",
      "Canonical entity confirmed; duplicate representations no longer need separate active rows",
    );
    if (!reason?.trim()) return;
    setManaging(`merge:${entity.group.id}`);
    setUploadError(null);
    try {
      await mergeDeleteEntityGroup(entity.group.id, entity.group.row_version, reason.trim());
      await refreshManagement();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setManaging(null);
    }
  };

  const dissolveGroup = async (entity: StoredEntity) => {
    if (!entity.group) return;
    const reason = window.prompt("Why should this duplicate group be dissolved?");
    if (!reason?.trim()) return;
    setManaging(entity.group.id);
    setUploadError(null);
    try {
      await dissolveEntityGroup(entity.group.id, entity.group.row_version, reason.trim());
      await refreshManagement();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setManaging(null);
    }
  };

  const visibleItems = (items ?? []).filter(
    (entity) =>
      !entity.group ||
      entity.group.canonical_entity_id === entity.id ||
      expandedGroups.has(entity.group.id),
  );

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      // Route by extension. .mtgl / .mtgx → Maltego (zip-of-GraphML);
      // .json/.csv → DarkWeb (Dehashed today, more sources later).
      const name = file.name.toLowerCase();
      if (name.endsWith(".mtgl") || name.endsWith(".mtgx")) {
        const result = await importEntitiesMaltego(slug, file);
        setLastImport({ kind: "maltego", result });
        qc.setQueryData<StoredEntity[]>(
          qk.storedEntities(slug),
          result.entities,
        );
      } else if (name.endsWith(".json") || name.endsWith(".csv")) {
        const result = await importEntitiesDarkweb(slug, file, "dehashed");
        setLastImport({ kind: "darkweb", result });
        qc.setQueryData<StoredEntity[]>(
          qk.storedEntities(slug),
          result.entities,
        );
      } else {
        setUploadError(
          "Unrecognized file type — upload .mtgl / .mtgx (Maltego), .json or .csv (Dehashed).",
        );
        return;
      }
      await refreshManagement();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-medium">Imported</h2>
          <p className="text-xs text-muted-foreground">
            Persistent entities from external sources. Accepts Maltego
            graphs (<code className="font-mono">.mtgl</code> or{" "}
            <code className="font-mono">.mtgx</code>) and Dehashed /
            DarkWeb exports (<code className="font-mono">.json</code> or{" "}
            <code className="font-mono">.csv</code>). Re-imports merge into
            existing rows.
          </p>
        </div>
        {canWrite ? (
          <div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
            >
              <Upload className="mr-1.5 h-3.5 w-3.5" />
              {uploading ? "Importing…" : "Import"}
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept=".mtgl,.mtgx,.json,.csv,application/zip,application/json,text/csv"
              className="hidden"
              onChange={onFile}
            />
          </div>
        ) : (
          <Badge variant="outline">Read-only</Badge>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={showRemoved}
            onChange={(event) => setShowRemoved(event.target.checked)}
          />
          Show removed records
        </label>
        <span className="text-[11px] text-muted-foreground">
          Conservative domain, IP, URL, email, and hash variants reuse existing records.
        </span>
      </div>

      {canWrite && duplicateCandidates.length > 0 && (
        <section className="space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
          <div>
            <h3 className="text-sm font-medium">Possible duplicate entities</h3>
            <p className="text-xs text-muted-foreground">
              Grouping retains every row, property, source, and finding link.
            </p>
          </div>
          {duplicateCandidates.map((candidate) => {
            const key = `${candidate.type}:${candidate.normalized_value}`;
            const selectedCanonical = canonicalChoices[key] ?? candidate.suggested_canonical_entity_id;
            return (
              <div key={key} className="rounded border border-border bg-background p-3 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <span className="font-medium">{typeLabel(candidate.type)}</span>{" "}
                    <span className="font-mono">{candidate.normalized_value}</span>
                    <p className="mt-1 text-muted-foreground">
                      {candidate.entities.map((item) => item.value).join(" · ")}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-1">
                      Canonical
                      <select
                        value={selectedCanonical}
                        onChange={(event) => setCanonicalChoices((current) => ({
                          ...current,
                          [key]: event.target.value,
                        }))}
                        className="h-8 max-w-48 rounded border border-input bg-background px-2 font-mono"
                      >
                        {candidate.entities.map((item) => (
                          <option key={item.id} value={item.id}>{item.value}</option>
                        ))}
                      </select>
                    </label>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={managing === `candidate:${key}`}
                      onClick={() => void groupCandidate(candidate)}
                    >
                      <Layers className="mr-1.5 h-3.5 w-3.5" /> Group duplicates
                    </Button>
                  </div>
                </div>
              </div>
            );
          })}
        </section>
      )}

      {lastImport?.kind === "maltego" && (
        <div className="rounded border border-border bg-background p-2 text-xs">
          <div className="font-medium">
            Maltego: <span className="font-mono">{lastImport.result.inserted}</span>{" "}
            inserted, <span className="font-mono">{lastImport.result.merged}</span>{" "}
            merged
            <span className="text-muted-foreground">
              {" "}
              ({lastImport.result.total_nodes} node
              {lastImport.result.total_nodes === 1 ? "" : "s"} in graph)
            </span>
          </div>
          {(lastImport.result.skipped_empty > 0 ||
            lastImport.result.skipped_unknown > 0) && (
            <div className="text-muted-foreground">
              Skipped:{" "}
              <span className="font-mono">
                {lastImport.result.skipped_empty}
              </span>{" "}
              empty ·{" "}
              <span className="font-mono">
                {lastImport.result.skipped_unknown}
              </span>{" "}
              unknown
            </div>
          )}
        </div>
      )}

      {lastImport?.kind === "darkweb" && (
        <div className="rounded border border-border bg-background p-2 text-xs">
          <div className="font-medium">
            {lastImport.result.source}:{" "}
            <span className="font-mono">{lastImport.result.inserted}</span>{" "}
            inserted, <span className="font-mono">{lastImport.result.merged}</span>{" "}
            merged
            <span className="text-muted-foreground">
              {" "}
              ({lastImport.result.total_rows} record
              {lastImport.result.total_rows === 1 ? "" : "s"})
            </span>
          </div>
          {lastImport.result.databases.length > 0 && (
            <div className="text-muted-foreground">
              Breach sources:{" "}
              <span className="font-mono">
                {lastImport.result.databases.join(", ")}
              </span>
            </div>
          )}
          {(lastImport.result.skipped_no_identifier > 0 ||
            lastImport.result.skipped_malformed > 0) && (
            <div className="text-muted-foreground">
              Skipped:{" "}
              <span className="font-mono">
                {lastImport.result.skipped_no_identifier}
              </span>{" "}
              no-identifier ·{" "}
              <span className="font-mono">
                {lastImport.result.skipped_malformed}
              </span>{" "}
              malformed
            </div>
          )}
        </div>
      )}

      {uploadError && (
        <p className="text-xs text-critical">{uploadError}</p>
      )}
      {loadError && <p className="text-xs text-critical">{loadError}</p>}

      {items === undefined ? (
        loadError ? null : (
          <p className="text-sm text-muted-foreground">
            Loading imported entities…
          </p>
        )
      ) : visibleItems.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No imported entities yet — upload a Maltego .mtgl / .mtgx to populate.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th className="px-3 py-2 w-28">Type</th>
                <th className="px-3 py-2">Value</th>
                <th className="px-3 py-2 w-40">Source</th>
                <th className="px-3 py-2 w-40 text-right">Manage</th>
              </tr>
            </thead>
            <tbody>
              {visibleItems.map((e) => (
                <tr
                  key={e.id}
                  className={cn(
                    "border-b border-border/60 last:border-0",
                    e.suppressed && "opacity-60",
                  )}
                >
                  <td className="px-3 py-2.5 text-xs text-muted-foreground">
                    {typeLabel(e.type)}
                  </td>
                  <td className="break-all px-3 py-2.5 font-mono text-xs">
                    <Link
                      href={`/e/entities?slug=${encodeURIComponent(slug)}&type=${encodeURIComponent(e.type)}&value=${encodeURIComponent(e.value)}`}
                      className="rounded-sm hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {e.value}
                    </Link>
                    {e.suppressed && (
                      <Badge variant="outline" className="ml-2 font-sans">Removed</Badge>
                    )}
                    {e.group?.canonical_entity_id === e.id && (
                      <button
                        type="button"
                        className="ml-2 rounded-sm font-sans text-[11px] text-muted-foreground underline-offset-2 hover:underline"
                        onClick={() => setExpandedGroups((current) => {
                          const next = new Set(current);
                          if (next.has(e.group!.id)) next.delete(e.group!.id);
                          else next.add(e.group!.id);
                          return next;
                        })}
                      >
                        Grouped · {e.group.member_count} records
                      </button>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-xs text-muted-foreground">
                    {e.finding_refs.length > 0 ? (
                      <span className="flex flex-col gap-0.5">
                        {e.finding_refs.map((finding) => (
                          <Link
                            key={finding.id}
                            href={`/e/findings/${finding.id}?slug=${encodeURIComponent(slug)}`}
                            className="max-w-48 truncate rounded-sm hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            title={`Promoted from ${finding.title}`}
                          >
                            {finding.title}
                          </Link>
                        ))}
                      </span>
                    ) : (
                      e.source_attribution ?? e.source_tool
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {!canWrite ? (
                      <span className="text-[11px] text-muted-foreground">
                        Read-only
                      </span>
                    ) : e.group?.canonical_entity_id === e.id ? (
                      <div className="flex justify-end gap-1">
                        {e.group.suppressed_member_count < e.group.member_count - 1 ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            disabled={managing === `merge:${e.group.id}`}
                            onClick={() => void mergeDeleteGroup(e)}
                          >
                            <Trash2 className="mr-1.5 h-3.5 w-3.5" /> Merge & remove
                          </Button>
                        ) : (
                          <span className="self-center text-[11px] text-muted-foreground">
                            Duplicates removed
                          </span>
                        )}
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          disabled={managing === e.group.id}
                          onClick={() => void dissolveGroup(e)}
                        >
                          Dissolve
                        </Button>
                      </div>
                    ) : e.suppressed ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={managing === e.id}
                        onClick={() => void disposeEntity(e)}
                      >
                        <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Restore
                      </Button>
                    ) : e.group ? (
                      <span className="text-[11px] text-muted-foreground">Grouped member</span>
                    ) : (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={managing === e.id}
                        onClick={() => void disposeEntity(e)}
                      >
                        <Trash2 className="mr-1.5 h-3.5 w-3.5" /> Remove
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}


// v2.21.0: pull lat/lon out of a freeipapi finding whose target matches
// this IP entity. Returns null if the entity isn't an IP, or if no
// enrichment has been run against it yet, or the enrichment lacks coords.
// v2.22.0: also accepts ipinfo findings as a fallback source. freeipapi
// is preferred (full country name), but ipinfo's lat/lon works too.
function useIpThumbnailPoint(
  entity: Entity,
  slug: string,
): { point: MapPoint; location: string } | null {
  const { data: findings = [] } = useFindings(slug);
  return useMemo(() => {
    if (entity.type !== "ip") return null;
    // Prefer freeipapi (richer country/region strings); fall back to ipinfo.
    const preferredOrder: Array<"freeipapi" | "ipinfo"> = ["freeipapi", "ipinfo"];
    for (const preferredTool of preferredOrder) {
      for (const f of findings) {
        if (f.tool !== preferredTool) continue;
        const data = f.data as Record<string, unknown> | undefined;
        const items = data?.items;
        if (!Array.isArray(items) || items.length === 0) continue;
        const item = items[0] as Record<string, unknown>;
        const targetIp = (item.ip as string | undefined) || f.target || "";
        if (targetIp !== entity.value) continue;
        const lat = coerceFloat(item.latitude);
        const lon = coerceFloat(item.longitude);
        if (lat === null || lon === null) continue;
        const parts = [
          item.city_name as string | undefined,
          item.region_name as string | undefined,
          (item.country_name as string | undefined) ??
            (item.country_code as string | undefined),
        ].filter((v): v is string => Boolean(v));
        return {
          point: { id: entity.value, lat, lon, label: entity.value },
          location: parts.join(", ") || "Location unknown",
        };
      }
    }
    return null;
  }, [entity, findings]);
}

function coerceFloat(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function EntitySlideOver({
  entity,
  slug,
  onClose,
  onQuickAction,
  onLaunchPlaybook,
  canWrite,
  scopeItems,
  scopeSaving,
  scopeMessage,
  scopeError,
  onAssignScope,
  onRemoveScopeRules,
  doneTools,
}: {
  entity: Entity;
  slug: string;
  onClose: () => void;
  onQuickAction?: (prompt: string) => void;
  onLaunchPlaybook?: (target: { type: string; value: string }) => void;
  canWrite: boolean;
  scopeItems: ScopeItem[];
  scopeSaving: boolean;
  scopeMessage: string | null;
  scopeError: string | null;
  onAssignScope: (disposition: "include" | "exclude") => void;
  onRemoveScopeRules: (items: ScopeItem[]) => void;
  doneTools: Set<string>;
}) {
  // v1.4.14: engagement-aware quick actions (roadmap #8). The chain is
  // ordered; the first step whose tool HASN'T produced a finding against
  // this entity is the "suggested next" (primary). Completed steps dim
  // with a check so the analyst sees what's left.
  const chain = ENTITY_ACTION_CHAINS[entity.type] ?? [];
  const nextStep = chain.find((a) => a.tool && !doneTools.has(a.tool));
  const ipThumbnail = useIpThumbnailPoint(entity, slug);
  const scopeTarget = scopeTargetForEntity(entity);
  const {
    rules: exactRules,
    exactIncludes,
    exactExclusions,
    canAdd,
    canExclude,
    isIncluded,
  } = scopeActionState(entity, scopeItems);
  const matchedScopeRule = entity.effective_scope
    ? scopeItems.find(
        (item) =>
          item.id === entity.effective_scope?.matched_exclusion_id ||
          item.id === entity.effective_scope?.matched_include_id,
      )
    : undefined;
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="left-auto right-0 top-0 flex h-dvh w-full max-w-lg translate-x-0 translate-y-0 flex-col gap-0 rounded-none border-y-0 border-r-0 p-0 sm:rounded-none">
        <DialogHeader className="border-b border-border px-6 py-5 pr-12">
          <div className="text-xs uppercase tracking-wide text-muted-foreground">
            {typeLabel(entity.type)}
          </div>
          <DialogTitle className="break-all font-mono text-lg leading-tight">
            {entity.value}
          </DialogTitle>
          <DialogDescription>
            Entity preview with scope status, finding provenance, and available actions.
          </DialogDescription>
          <div className="flex flex-wrap items-center gap-2 pt-2">
            <Badge variant="outline" className={SEVERITY_CLASS[entity.severity]}>
              {entity.severity}
            </Badge>
            <ScopeStatusBadge status={entity.scope_status} />
            {entity.relevance === "likely_third_party" ? (
              <Badge
                variant="outline"
                className="border-violet-500/40 text-violet-700 dark:text-violet-300"
                title={entity.relevance_reason ?? undefined}
              >
                Likely third-party
              </Badge>
            ) : null}
            <span className="text-xs text-muted-foreground">
              {entity.count} finding{entity.count === 1 ? "" : "s"}
            </span>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {entity.relevance === "likely_third_party" && entity.relevance_reason ? (
            <p className="mb-4 rounded-md border border-violet-500/30 bg-violet-500/5 px-3 py-2 text-xs text-muted-foreground">
              {entity.relevance_reason}. Kept for review because vendor infrastructure
              can still be authorized for an engagement.
            </p>
          ) : null}

          <section className="space-y-3 rounded-lg border border-border bg-card/40 p-4">
            <div>
              <h3 className="text-sm font-medium">Scope assignment</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                Add this exact target to authorized scope or create an explicit exclusion.
                Broader domain and CIDR exclusions remain authoritative.
              </p>
            </div>
            {entity.effective_scope && (
              <div className="rounded-md border border-border/70 bg-muted/20 px-3 py-2 text-xs">
                <p>{entity.effective_scope.reason}</p>
                {matchedScopeRule && (
                  <p className="mt-1 font-mono text-muted-foreground">
                    Matched {matchedScopeRule.kind}: {matchedScopeRule.value}
                  </p>
                )}
              </div>
            )}
            {!scopeTarget ? (
              <p className="text-xs text-muted-foreground">
                {typeLabel(entity.type)} entities cannot be used as scope targets.
              </p>
            ) : canWrite ? (
              <div className="flex flex-wrap gap-2">
                {exactExclusions.length > 0 ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => onRemoveScopeRules(exactExclusions)}
                    disabled={scopeSaving}
                  >
                    Remove exclusion
                  </Button>
                ) : exactIncludes.length > 0 ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => onRemoveScopeRules(exactIncludes)}
                    disabled={scopeSaving}
                  >
                    Remove from scope
                  </Button>
                ) : (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => onAssignScope("include")}
                      disabled={!canAdd || scopeSaving}
                      title={isIncluded ? "Already included by current scope." : undefined}
                    >
                      {scopeSaving ? (
                        <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <ListPlus className="mr-1.5 h-3.5 w-3.5" />
                      )}
                      {isIncluded ? "In scope" : "Add to scope"}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => onAssignScope("exclude")}
                      disabled={!canExclude || scopeSaving}
                    >
                      <Ban className="mr-1.5 h-3.5 w-3.5" />
                      Exclude
                    </Button>
                  </>
                )}
              </div>
            ) : (
              <Badge variant="outline">Read-only</Badge>
            )}

            {scopeMessage && (
              <p className="text-xs text-emerald-700 dark:text-emerald-300" role="status">
                {scopeMessage}
              </p>
            )}
            {scopeError && (
              <p className="text-xs text-critical" role="alert">
                {scopeError}
              </p>
            )}

            {onLaunchPlaybook &&
              canWrite &&
              entity.effective_scope?.allowed !== false &&
              entity.scope_status === "live" &&
              exactIncludes.length > 0 && (
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    onLaunchPlaybook({ type: entity.type, value: entity.value })
                  }
                >
                  <Zap className="mr-1.5 h-3.5 w-3.5" />
                  Run a playbook for this target
                </Button>
              )}

            {exactRules.length > 0 && (
              <div className="space-y-2 border-t border-border pt-3">
                <div className="text-xs uppercase tracking-wide text-muted-foreground">
                  Exact rules
                </div>
                {exactIncludes.length > 0 && (
                  <div className="flex items-center justify-between gap-3 text-xs">
                    <span>
                      In scope · {exactIncludes.length} exact {exactIncludes.length === 1 ? "rule" : "rules"}
                    </span>

                  </div>
                )}
                {exactExclusions.length > 0 && (
                  <div className="flex items-center justify-between gap-3 text-xs">
                    <span>
                      Excluded · {exactExclusions.length} exact {exactExclusions.length === 1 ? "rule" : "rules"}
                    </span>

                  </div>
                )}
              </div>
            )}
          </section>

          {ipThumbnail && (
            <div className="mt-5 space-y-2">
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                Geolocation
              </div>
              <LeafletMap
                points={[ipThumbnail.point]}
                height={180}
                interactive={false}
                initialZoom={4}
              />
              <p className="text-xs text-muted-foreground">
                {ipThumbnail.location} · {ipThumbnail.point.lat.toFixed(4)},{" "}
                {ipThumbnail.point.lon.toFixed(4)}
              </p>
            </div>
          )}

          {onQuickAction && chain.length > 0 && (
            <div className="mt-5 space-y-2">
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                {nextStep ? "Suggested next" : "Recon actions"}
              </div>
              <div className="flex flex-wrap gap-2">
                {chain.map((action) => {
                  const done = action.tool != null && doneTools.has(action.tool);
                  const isNext = action === nextStep;
                  return (
                    <Button
                      key={action.label}
                      size="sm"
                      variant={isNext ? "default" : "outline"}
                      className={done && !isNext ? "opacity-70" : ""}
                      onClick={() => onQuickAction(action.prompt(entity.value))}
                      title={
                        done
                          ? `Already run (${action.tool}) — click to re-run`
                          : action.label
                      }
                    >
                      {isNext ? (
                        <Zap className="mr-1.5 h-3.5 w-3.5" />
                      ) : done ? (
                        <Check className="mr-1.5 h-3.5 w-3.5" />
                      ) : null}
                      {action.label}
                    </Button>
                  );
                })}
              </div>
              {!nextStep && chain.some((action) => action.tool) && (
                <p className="text-xs text-muted-foreground">
                  Every recon step has finding evidence. Re-run only when you
                  need refreshed data.
                </p>
              )}
            </div>
          )}

          <h3 className="mt-6 text-sm font-medium">Finding provenance</h3>
          {entity.findings.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">
              This entity comes from engagement scope; no finding has referenced it yet.
            </p>
          ) : (
            <ul className="mt-2 space-y-2">
              {entity.findings.map((finding) => (
                <li key={finding.id}>
                  <Link
                    href={`/e/findings/${finding.id}?slug=${encodeURIComponent(slug)}`}
                    className="block rounded-md border border-border px-3 py-2 text-sm transition-colors hover:border-foreground/30 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <div className="font-medium leading-tight">{finding.title}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {finding.tool ?? "manual"} · {finding.phase} · {finding.severity}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>

        <DialogFooter className="border-t border-border px-6 py-4">
          <Button asChild>
            <Link
              href={`/e/entities?slug=${encodeURIComponent(slug)}&type=${encodeURIComponent(entity.type)}&value=${encodeURIComponent(entity.value)}`}
            >
              Open full entity view
            </Link>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
