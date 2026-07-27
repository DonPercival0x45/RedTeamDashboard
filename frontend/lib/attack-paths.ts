import { extractDossierRelationships } from "@/lib/dossier";
import { effectiveScopeState } from "@/lib/effective-scope";
import type {
  Entity,
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
  outOfScope: boolean;
  sourceExcluded: boolean;
  targetExcluded: boolean;
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
  outOfScope: boolean;
  excludedEntityCount: number;
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
      outOfScope: finding.exclusion != null,
      sourceExcluded: false,
      targetExcluded: false,
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
    existing.outOfScope = existing.citations.every(
      (citation) => citation.exclusion != null,
    );
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
function buildAttackPathProjectionFromEdges(
  edges: AttackPathEdge[],
  maxEdges = 4,
  maxPaths = 500,
): AttackPathProjection {
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
  type WalkState = {
    chain: AttackPathEdge[];
    usedEdges: Set<string>;
    visitedNodes: Set<string>;
    outOfScope: boolean;
  };
  const activeQueue: WalkState[] = [];
  const excludedQueue: WalkState[] = [];
  const chains: AttackPathEdge[][] = [];
  let activeChains = 0;
  let excludedChains = 0;
  let visitedStates = 0;
  let truncated = false;
  const hasExcludedEdges = edges.some((edge) => edge.outOfScope);
  const maxStates = Math.max(1_000, maxPaths * 50);

  const enqueue = (state: WalkState) => {
    if (state.outOfScope) excludedQueue.push(state);
    else activeQueue.push(state);
  };
  const seed = (edge: AttackPathEdge) => {
    enqueue({
      chain: [edge],
      usedEdges: new Set([edge.id]),
      visitedNodes: new Set([
        nodeKey(edge.sourceType, edge.sourceValue),
        nodeKey(edge.targetType, edge.targetValue),
      ]),
      outOfScope: edge.outOfScope,
    });
  };
  const record = (state: WalkState) => {
    if (state.outOfScope) {
      if (excludedChains >= maxPaths) {
        truncated = true;
        return;
      }
      excludedChains += 1;
    } else {
      if (activeChains >= maxPaths) {
        truncated = true;
        return;
      }
      activeChains += 1;
    }
    chains.push(state.chain);
  };
  const drain = (queue: WalkState[]) => {
    for (let index = 0; index < queue.length; index += 1) {
      if (visitedStates >= maxStates) {
        truncated = true;
        break;
      }
      if (
        activeChains >= maxPaths &&
        (!hasExcludedEdges || excludedChains >= maxPaths)
      ) {
        truncated = true;
        break;
      }
      const state = queue[index];
      if (state.outOfScope && excludedChains >= maxPaths) {
        truncated = true;
        continue;
      }
      visitedStates += 1;
      const last = state.chain[state.chain.length - 1];
      const next = (
        outgoing.get(nodeKey(last.targetType, last.targetValue)) ?? []
      ).filter(
        (edge) =>
          !state.usedEdges.has(edge.id) &&
          !state.visitedNodes.has(nodeKey(edge.targetType, edge.targetValue)),
      );
      if (state.chain.length >= maxEdges || next.length === 0) {
        record(state);
        continue;
      }
      for (const edge of next) {
        const outOfScope = state.outOfScope || edge.outOfScope;
        if (outOfScope && excludedChains >= maxPaths) {
          truncated = true;
          continue;
        }
        enqueue({
          chain: [...state.chain, edge],
          usedEdges: new Set([...state.usedEdges, edge.id]),
          visitedNodes: new Set([
            ...state.visitedNodes,
            nodeKey(edge.targetType, edge.targetValue),
          ]),
          outOfScope,
        });
      }
    }
    queue.length = 0;
  };

  for (const edge of starts) seed(edge);
  // Drain all-active states across every root before excluded continuations.
  // A broad provider branch therefore cannot consume the active-path budget.
  drain(activeQueue);
  drain(excludedQueue);

  const covered = new Set(chains.flatMap((chain) => chain.map((edge) => edge.id)));
  // A disconnected cyclic component has no root. Retain it in its matching
  // bounded bucket instead of silently dropping observed evidence.
  for (const edge of edges) {
    if (!covered.has(edge.id)) seed(edge);
  }
  drain(activeQueue);
  drain(excludedQueue);

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
    const excludedEntityKeys = new Set(
      chain.flatMap((edge) => [
        ...(edge.sourceExcluded
          ? [nodeKey(edge.sourceType, edge.sourceValue)]
          : []),
        ...(edge.targetExcluded
          ? [nodeKey(edge.targetType, edge.targetValue)]
          : []),
      ]),
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
      outOfScope: chain.some((edge) => edge.outOfScope),
      excludedEntityCount: excludedEntityKeys.size,
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

export function buildAttackPathProjection(
  findings: Finding[],
  maxEdges = 4,
  maxPaths = 500,
): AttackPathProjection {
  return buildAttackPathProjectionFromEdges(
    buildAttackPathEdges(findings),
    maxEdges,
    maxPaths,
  );
}

function excludedEntityKeys(entities: Entity[]): Set<string> {
  return new Set(
    entities
      .filter(
        (entity) =>
          effectiveScopeState(entity) === "excluded" ||
          entity.review_disposition === "excluded",
      )
      .map((entity) => nodeKey(entity.type, entity.value)),
  );
}

function applyEntityScopeToEdges(
  edges: AttackPathEdge[],
  entities: Entity[],
): AttackPathEdge[] {
  const excluded = excludedEntityKeys(entities);
  return edges.map((edge) => {
    const sourceExcluded = excluded.has(
      nodeKey(edge.sourceType, edge.sourceValue),
    );
    const targetExcluded = excluded.has(
      nodeKey(edge.targetType, edge.targetValue),
    );
    return {
      ...edge,
      sourceExcluded,
      targetExcluded,
      outOfScope: edge.outOfScope || sourceExcluded || targetExcluded,
    };
  });
}

export function buildScopeAwareAttackPathProjection(
  findings: Finding[],
  entities: Entity[],
  maxEdges = 4,
  maxPaths = 500,
): AttackPathProjection {
  const edges = applyEntityScopeToEdges(buildAttackPathEdges(findings), entities);
  // Active roots sort before excluded provider branches so a broad shared-
  // infrastructure branch cannot consume the bounded projection first.
  edges.sort(
    (a, b) => Number(a.outOfScope) - Number(b.outOfScope) || a.id.localeCompare(b.id),
  );
  return buildAttackPathProjectionFromEdges(edges, maxEdges, maxPaths);
}

/**
 * Apply authoritative Entity scope dispositions without deleting evidence.
 * Only explicit exclusions are suppressive; generic unmatched/OOS entities
 * remain visible for analyst review.
 */
export function applyEntityScopeToAttackPaths(
  projection: AttackPathProjection,
  entities: Entity[],
): AttackPathProjection {
  const excluded = excludedEntityKeys(entities);
  return {
    ...projection,
    paths: projection.paths.map((path) => {
      const excludedNodes = new Set(
        path.nodes
          .map((node) => nodeKey(node.type, node.value))
          .filter((key) => excluded.has(key)),
      );
      const edges = path.edges.map((edge) => {
        const sourceExcluded = excluded.has(
          nodeKey(edge.sourceType, edge.sourceValue),
        );
        const targetExcluded = excluded.has(
          nodeKey(edge.targetType, edge.targetValue),
        );
        return {
          ...edge,
          sourceExcluded,
          targetExcluded,
          outOfScope: edge.outOfScope || sourceExcluded || targetExcluded,
        };
      });
      return {
        ...path,
        edges,
        outOfScope:
          excludedNodes.size > 0 || edges.some((edge) => edge.outOfScope),
        excludedEntityCount: excludedNodes.size,
      };
    }),
  };
}

export function buildAttackPaths(findings: Finding[], maxEdges = 4): AttackPath[] {
  return buildAttackPathProjection(findings, maxEdges).paths;
}
