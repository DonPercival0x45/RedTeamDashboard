import type {
  Finding,
  Observation,
  PlaybookRunRead,
} from "@/lib/types";

export type DossierRelationshipKind =
  | "resolves_to"
  | "aliases_to"
  | "delegates_to";

export interface DossierRelationship {
  id: string;
  sourceType: "domain" | "subdomain" | "host";
  sourceValue: string;
  targetType: "domain" | "host" | "ip";
  targetValue: string;
  kind: DossierRelationshipKind;
  observedAt: string;
  findingId: string;
  findingTitle: string;
  sourceTool: string | null;
}

export type DossierTimelineKind =
  | "relationship"
  | "finding"
  | "observation"
  | "run";

export interface DossierTimelineItem {
  id: string;
  kind: DossierTimelineKind;
  occurredAt: string;
  title: string;
  description: string;
  sourceLabel: string;
  findingId?: string;
  runId?: string;
  entityType?: string;
  entityValue?: string;
  trust: "observed" | "analyst" | "execution" | "record";
}

const IP_RE = /^(?:\d{1,3}\.){3}\d{1,3}$/;
const STRUCTURED_DNS_TOOLS = new Set([
  "dns_lookup",
  "dns_inventory",
  "dns-inventory",
  "mcp_dns_lookup",
]);

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function asStringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map(asString)
      .filter((item): item is string => item !== null);
  }
  const scalar = asString(value);
  return scalar ? [scalar] : [];
}

function entityType(value: string): "domain" | "host" | "ip" {
  if (IP_RE.test(value)) return "ip";
  return value.includes(".") ? "domain" : "host";
}

/**
 * Extract only relationships explicitly represented by structured DNS output.
 * This deliberately avoids correlating arbitrary strings from narrative text.
 */
export function extractDossierRelationships(
  findings: Finding[],
): DossierRelationship[] {
  const relationships = new Map<string, DossierRelationship>();

  for (const finding of findings) {
    const parentIsDns = Boolean(
      finding.tool && STRUCTURED_DNS_TOOLS.has(finding.tool.toLowerCase()),
    );
    const items = Array.isArray(finding.data?.items)
      ? finding.data.items
      : [];
    for (let index = 0; index < items.length; index += 1) {
      const item = asRecord(items[index]);
      if (!item) continue;
      const itemTool = asString(item.source_tool)?.toLowerCase();
      const itemIsDns = Boolean(itemTool && STRUCTURED_DNS_TOOLS.has(itemTool));
      if (!parentIsDns && !itemIsDns) continue;
      const sourceCandidates: Array<{
        type: DossierRelationship["sourceType"];
        value: string | null;
      }> = [
        { type: "subdomain", value: asString(item.subdomain) },
        { type: "domain", value: asString(item.domain) },
        { type: "host", value: asString(item.hostname) },
        { type: "host", value: asString(item.host) },
        {
          type:
            (finding.target?.split(".").filter(Boolean).length ?? 0) > 2
              ? "subdomain"
              : "domain",
          value: finding.target,
        },
      ];
      const source = sourceCandidates.find((candidate) => candidate.value);
      if (!source?.value) continue;
      const observedAt =
        asString(item.first_seen_at) ??
        finding.observed_at ??
        finding.created_at;

      const candidates: Array<{
        kind: DossierRelationshipKind;
        value: string;
      }> = [];
      for (const value of [...asStringList(item.a), ...asStringList(item.aaaa)]) {
        candidates.push({ kind: "resolves_to", value });
      }
      for (const value of asStringList(item.cname)) {
        candidates.push({ kind: "aliases_to", value });
      }
      for (const value of asStringList(item.ns)) {
        candidates.push({ kind: "delegates_to", value });
      }
      const recordType = asString(item.type)?.toUpperCase();
      const recordValue = asString(item.value);
      if (recordValue && (recordType === "A" || recordType === "AAAA")) {
        candidates.push({ kind: "resolves_to", value: recordValue });
      } else if (recordValue && recordType === "CNAME") {
        candidates.push({ kind: "aliases_to", value: recordValue });
      } else if (recordValue && recordType === "NS") {
        candidates.push({ kind: "delegates_to", value: recordValue });
      }

      for (const candidate of candidates) {
        const key = `${finding.id}:${source.value}:${candidate.kind}:${candidate.value}`;
        const previous = relationships.get(key);
        if (previous && previous.observedAt <= observedAt) continue;
        relationships.set(key, {
          id: `${finding.id}:${index}:${candidate.kind}:${candidate.value}`,
          sourceType: source.type,
          sourceValue: source.value,
          targetType: entityType(candidate.value),
          targetValue: candidate.value,
          kind: candidate.kind,
          observedAt,
          findingId: finding.id,
          findingTitle: finding.title,
          sourceTool: finding.tool,
        });
      }
    }
  }

  return Array.from(relationships.values()).sort((a, b) =>
    b.observedAt.localeCompare(a.observedAt),
  );
}

function runLabel(slug: string): string {
  return slug
    .split("-")
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

export function buildDossierTimeline(
  findings: Finding[],
  observations: Observation[],
  runs: PlaybookRunRead[],
  relationships: DossierRelationship[],
): DossierTimelineItem[] {
  const rows: DossierTimelineItem[] = [];

  for (const relationship of relationships) {
    const verb =
      relationship.kind === "resolves_to"
        ? "resolved to"
        : relationship.kind === "aliases_to"
          ? "aliased to"
          : "delegated to";
    rows.push({
      id: `relationship:${relationship.id}`,
      kind: "relationship",
      occurredAt: relationship.observedAt,
      title: `${relationship.sourceValue} ${verb} ${relationship.targetValue}`,
      description: `Structured ${relationship.sourceTool ?? "DNS"} evidence in “${relationship.findingTitle}”.`,
      sourceLabel: relationship.sourceTool ?? "DNS evidence",
      findingId: relationship.findingId,
      entityType: relationship.targetType,
      entityValue: relationship.targetValue,
      trust: "observed",
    });
  }

  const relationshipFindingIds = new Set(
    relationships.map((relationship) => relationship.findingId),
  );
  for (const finding of findings) {
    // A structured relationship is more informative than a duplicate generic
    // row for the same grouped DNS finding.
    if (relationshipFindingIds.has(finding.id)) continue;
    rows.push({
      id: `finding:${finding.id}`,
      kind: "finding",
      occurredAt: finding.observed_at ?? finding.created_at,
      title: finding.title,
      description: finding.target
        ? `${finding.tool ?? "Manual finding"} recorded ${finding.target} · ${finding.status.replaceAll("_", " ")}.`
        : `${finding.tool ?? "Manual finding"} record · ${finding.status.replaceAll("_", " ")}.`,
      sourceLabel: finding.tool ?? "Finding record",
      findingId: finding.id,
      entityValue: finding.target ?? undefined,
      trust: "record",
    });
  }

  for (const observation of observations) {
    rows.push({
      id: `observation:${observation.id}`,
      kind: "observation",
      occurredAt: observation.created_at,
      title: "Analyst observation",
      description: observation.content,
      sourceLabel: observation.created_by
        ? `Analyst · ${observation.created_by}`
        : "Analyst note",
      findingId: observation.finding_ids?.[0],
      trust: "analyst",
    });
  }

  for (const run of runs) {
    const occurredAt = run.completed_at ?? run.started_at;
    if (!occurredAt) continue;
    rows.push({
      id: `run:${run.id}`,
      kind: "run",
      occurredAt,
      title: `${runLabel(run.playbook_slug)} · ${run.status}`,
      description: `${run.steps_succeeded} succeeded · ${run.steps_failed} failed · ${run.findings_new} new findings.`,
      sourceLabel: "Playbook execution",
      runId: run.id,
      trust: "execution",
    });
  }

  return rows.sort((a, b) => {
    const byTime = b.occurredAt.localeCompare(a.occurredAt);
    return byTime || a.id.localeCompare(b.id);
  });
}
