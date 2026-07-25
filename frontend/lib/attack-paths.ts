import { extractDossierRelationships } from "@/lib/dossier";
import type {
  Finding,
  FindingExclusion,
  FindingValidationStatus,
  Severity,
} from "@/lib/types";

const SEVERITY_RANK: Record<Severity, number> = {
  info: 0,
  low: 1,
  medium: 2,
  high: 3,
  critical: 4,
};

export interface AttackPathCitation {
  findingId: string;
  title: string;
  tool: string | null;
  status: FindingValidationStatus;
  exclusion: FindingExclusion | null;
  severity: Severity;
  observedAt: string;
}

export interface AttackPathEdge {
  id: string;
  sourceType: string;
  sourceValue: string;
  targetType: string;
  targetValue: string;
  relation: "resolves_to" | "aliases_to" | "delegates_to";
  citations: AttackPathCitation[];
  firstSeen: string;
  lastSeen: string;
  maxSeverity: Severity;
  needsValidation: boolean;
  disputed: boolean;
}

export interface AttackPath {
  id: string;
  edges: AttackPathEdge[];
  nodes: Array<{ type: string; value: string }>;
  firstSeen: string;
  lastSeen: string;
  maxSeverity: Severity;
  needsValidation: boolean;
  disputed: boolean;
  citationCount: number;
}

export interface AttackPathProjection {
  paths: AttackPath[];
  truncated: boolean;
}

function nodeKey(type: string, value: string): string {
  const family = ["domain", "subdomain", "host"].includes(type) ? "dns" : type;
  return `${family}:${value.trim().toLowerCase()}`;
}

function edgeKey(edge: Pick<AttackPathEdge, "sourceType" | "sourceValue" | "relation" | "targetType" | "targetValue">): string {
  return `${nodeKey(edge.sourceType, edge.sourceValue)}>${edge.relation}>${nodeKey(edge.targetType, edge.targetValue)}`;
}

function maxSeverity(values: Severity[]): Severity {
  return values.reduce(
    (best, value) =>
      SEVERITY_RANK[value] > SEVERITY_RANK[best] ? value : best,
    "info" as Severity,
  );
}

export function buildAttackPathEdges(findings: Finding[]): AttackPathEdge[] {
  const findingById = new Map(findings.map((finding) => [finding.id, finding]));
  const grouped = new Map<string, AttackPathEdge>();

  for (const relationship of extractDossierRelationships(findings)) {
    const finding = findingById.get(relationship.findingId);
    if (!finding) continue;
    const candidate: AttackPathEdge = {
      id: "",
      sourceType: relationship.sourceType,
      sourceValue: relationship.sourceValue,
      targetType: relationship.targetType,
      targetValue: relationship.targetValue,
      relation: relationship.kind,
      citations: [
        {
          findingId: finding.id,
          title: finding.title,
          tool: finding.tool,
          status: finding.status,
          exclusion: finding.exclusion ?? null,
          severity: finding.severity,
          observedAt: relationship.observedAt,
        },
      ],
      firstSeen: relationship.observedAt,
      lastSeen: relationship.observedAt,
      maxSeverity:
        finding.status === "rejected" || finding.status === "false_positive"
          ? "info"
          : finding.severity,
      needsValidation:
        finding.status === "pending_validation" || finding.status === "needs_review",
      disputed:
        finding.status === "rejected" || finding.status === "false_positive",
    };
    const key = edgeKey(candidate);
    const existing = grouped.get(key);
    if (!existing) {
      candidate.id = key;
      grouped.set(key, candidate);
      continue;
    }
    const existingDisplay = `${existing.sourceType}:${existing.sourceValue}>${existing.targetType}:${existing.targetValue}`;
    const candidateDisplay = `${candidate.sourceType}:${candidate.sourceValue}>${candidate.targetType}:${candidate.targetValue}`;
    if (candidateDisplay.localeCompare(existingDisplay) < 0) {
      existing.sourceType = candidate.sourceType;
      existing.sourceValue = candidate.sourceValue;
      existing.targetType = candidate.targetType;
      existing.targetValue = candidate.targetValue;
    }
    if (!existing.citations.some((citation) => citation.findingId === finding.id)) {
      existing.citations.push(candidate.citations[0]);
    }
    existing.firstSeen = existing.firstSeen < candidate.firstSeen ? existing.firstSeen : candidate.firstSeen;
    existing.lastSeen = existing.lastSeen > candidate.lastSeen ? existing.lastSeen : candidate.lastSeen;
    const admissible = existing.citations.filter(
      (citation) => citation.status !== "rejected" && citation.status !== "false_positive",
    );
    existing.maxSeverity = maxSeverity(admissible.map((citation) => citation.severity));
    existing.needsValidation = admissible.some(
      (citation) =>
        citation.status === "pending_validation" || citation.status === "needs_review",
    );
    existing.disputed = admissible.length === 0;
  }

  return Array.from(grouped.values())
    .map((edge) => ({
      ...edge,
      citations: edge.citations.sort(
        (a, b) => b.observedAt.localeCompare(a.observedAt) || a.findingId.localeCompare(b.findingId),
      ),
    }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

/** Build deterministic, bounded, acyclic paths from observed structured edges. */
export function buildAttackPathProjection(
  findings: Finding[],
  maxEdges = 4,
  maxPaths = 500,
): AttackPathProjection {
  const edges = buildAttackPathEdges(findings);
  const outgoing = new Map<string, AttackPathEdge[]>();
  const targetNodes = new Set<string>();
  for (const edge of edges) {
    const source = nodeKey(edge.sourceType, edge.sourceValue);
    const list = outgoing.get(source) ?? [];
    list.push(edge);
    outgoing.set(source, list);
    targetNodes.add(nodeKey(edge.targetType, edge.targetValue));
  }
  for (const list of outgoing.values()) list.sort((a, b) => a.id.localeCompare(b.id));

  const roots = edges.filter(
    (edge) => !targetNodes.has(nodeKey(edge.sourceType, edge.sourceValue)),
  );
  const starts = roots.length > 0 ? roots : edges;
  const chains: AttackPathEdge[][] = [];
  let truncated = false;

  const walk = (
    chain: AttackPathEdge[],
    usedEdges: Set<string>,
    visitedNodes: Set<string>,
  ) => {
    if (chains.length >= maxPaths) {
      truncated = true;
      return;
    }
    const last = chain[chain.length - 1];
    const next = (outgoing.get(nodeKey(last.targetType, last.targetValue)) ?? []).filter(
      (edge) =>
        !usedEdges.has(edge.id) &&
        !visitedNodes.has(nodeKey(edge.targetType, edge.targetValue)),
    );
    if (chain.length >= maxEdges || next.length === 0) {
      chains.push(chain);
      return;
    }
    for (const edge of next) {
      if (chains.length >= maxPaths) {
        truncated = true;
        break;
      }
      walk(
        [...chain, edge],
        new Set([...usedEdges, edge.id]),
        new Set([...visitedNodes, nodeKey(edge.targetType, edge.targetValue)]),
      );
    }
  };
  for (const edge of starts) {
    if (chains.length >= maxPaths) {
      truncated = true;
      break;
    }
    walk(
      [edge],
      new Set([edge.id]),
      new Set([
        nodeKey(edge.sourceType, edge.sourceValue),
        nodeKey(edge.targetType, edge.targetValue),
      ]),
    );
  }
  const covered = new Set(chains.flatMap((chain) => chain.map((edge) => edge.id)));
  // A disconnected cyclic component has no root. Retain it as a bounded path
  // instead of silently dropping observed evidence.
  for (const edge of edges) {
    if (chains.length >= maxPaths) {
      truncated = true;
      break;
    }
    if (!covered.has(edge.id)) {
      walk(
        [edge],
        new Set([edge.id]),
        new Set([
          nodeKey(edge.sourceType, edge.sourceValue),
          nodeKey(edge.targetType, edge.targetValue),
        ]),
      );
    }
  }

  const unique = new Map<string, AttackPath>();
  for (const chain of chains) {
    const id = chain.map((edge) => edge.id).join("|");
    if (unique.has(id)) continue;
    const nodes = [
      { type: chain[0].sourceType, value: chain[0].sourceValue },
      ...chain.map((edge) => ({ type: edge.targetType, value: edge.targetValue })),
    ];
    const citationIds = new Set(
      chain.flatMap((edge) => edge.citations.map((citation) => citation.findingId)),
    );
    unique.set(id, {
      id,
      edges: chain,
      nodes,
      firstSeen: chain.map((edge) => edge.firstSeen).sort()[0],
      lastSeen: chain.map((edge) => edge.lastSeen).sort().at(-1) ?? chain[0].lastSeen,
      maxSeverity: maxSeverity(chain.map((edge) => edge.maxSeverity)),
      needsValidation: chain.some((edge) => edge.needsValidation),
      disputed: chain.some((edge) => edge.disputed),
      citationCount: citationIds.size,
    });
  }
  const paths = Array.from(unique.values()).sort(
    (a, b) =>
      Number(b.disputed) - Number(a.disputed) ||
      Number(b.needsValidation) - Number(a.needsValidation) ||
      SEVERITY_RANK[b.maxSeverity] - SEVERITY_RANK[a.maxSeverity] ||
      b.lastSeen.localeCompare(a.lastSeen) ||
      a.id.localeCompare(b.id),
  );
  return { paths, truncated };
}

export function buildAttackPaths(findings: Finding[], maxEdges = 4): AttackPath[] {
  return buildAttackPathProjection(findings, maxEdges).paths;
}
