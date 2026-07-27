import type {
  FindingHierarchyFindingRef,
  FindingHierarchyItem,
  FindingWorkspaceBucket,
  FindingWorkspaceView,
  Severity,
} from "@/lib/types";

export const FINDING_WORKSPACE_VIEWS: Array<{
  value: FindingWorkspaceView;
  label: string;
}> = [
  { value: "focus", label: "Focus" },
  { value: "needs_review", label: "Needs review" },
  { value: "actionable", label: "Actionable Findings" },
  { value: "inventory", label: "Inventory" },
  { value: "resolved_excluded", label: "Resolved / Excluded" },
];

export const FINDING_WORKSPACE_VIEW_VALUES = new Set<FindingWorkspaceView>(
  FINDING_WORKSPACE_VIEWS.map((view) => view.value),
);

const SEVERITY_RANK: Record<Severity, number> = {
  info: 0,
  low: 1,
  medium: 2,
  high: 3,
  critical: 4,
};

export function refMatchesView(
  ref: FindingHierarchyFindingRef,
  view: FindingWorkspaceView,
): boolean {
  if (view === "focus") {
    return ref.bucket === "needs_review" || ref.bucket === "actionable";
  }
  return ref.bucket === (view as FindingWorkspaceBucket);
}

function visibleRollup(item: FindingHierarchyItem): FindingHierarchyItem["rollup"] {
  const refs = new Map<string, FindingHierarchyFindingRef>();
  const collect = (node: FindingHierarchyItem) => {
    node.finding_refs.forEach((ref) => refs.set(ref.id, ref));
    node.children.forEach(collect);
  };
  collect(item);
  const values = Array.from(refs.values());
  const counts = {
    needs_review: 0,
    actionable: 0,
    inventory: 0,
    resolved_excluded: 0,
  };
  let maxSeverity: Severity = "info";
  let latestAt: string | null = null;
  values.forEach((ref) => {
    counts[ref.bucket] += 1;
    const observed = ref.observed_at ?? ref.created_at;
    if (latestAt === null || observed > latestAt) latestAt = observed;
    if (
      ref.bucket !== "resolved_excluded" &&
      SEVERITY_RANK[ref.severity] > SEVERITY_RANK[maxSeverity]
    ) {
      maxSeverity = ref.severity;
    }
  });
  return {
    max_severity: maxSeverity,
    distinct_findings: values.length,
    latest_at: latestAt,
    ...counts,
  };
}

function textMatches(item: FindingHierarchyItem, query: string): boolean {
  if (!query) return true;
  const fields = [
    item.label,
    item.value,
    item.ip,
    item.hostname,
    item.protocol,
    item.port?.toString(),
    item.service,
    item.url,
    ...item.finding_refs.flatMap((ref) => [
      ref.id,
      ref.title,
      ref.tool,
      ref.target,
    ]),
  ];
  return fields.some((field) => field?.toLowerCase().includes(query));
}

export function filterHierarchyItem(
  item: FindingHierarchyItem,
  view: FindingWorkspaceView,
  rawQuery = "",
): FindingHierarchyItem | null {
  const query = rawQuery.trim().toLowerCase();
  const directRefs = item.finding_refs.filter((ref) => refMatchesView(ref, view));
  const childMatches = item.children
    .map((child) => filterHierarchyItem(child, view, rawQuery))
    .filter((child): child is FindingHierarchyItem => child !== null);
  const directTextMatch = textMatches(item, query);
  const hasViewContent = directRefs.length > 0 || childMatches.length > 0;
  if (!hasViewContent) return null;
  if (query && !directTextMatch && childMatches.length === 0) return null;
  const filtered: FindingHierarchyItem = {
    ...item,
    finding_refs: directRefs,
    children:
      query && directTextMatch
        ? item.children
            .map((child) => filterHierarchyItem(child, view, ""))
            .filter((child): child is FindingHierarchyItem => child !== null)
        : childMatches,
  };
  filtered.rollup = visibleRollup(filtered);
  return filtered;
}

export function filterHierarchy(
  items: FindingHierarchyItem[],
  view: FindingWorkspaceView,
  search = "",
): FindingHierarchyItem[] {
  return items
    .map((item) => filterHierarchyItem(item, view, search))
    .filter((item): item is FindingHierarchyItem => item !== null)
    .sort((a, b) => {
      const review = Number(b.rollup.needs_review > 0) - Number(a.rollup.needs_review > 0);
      if (review !== 0) return review;
      const severity =
        SEVERITY_RANK[b.rollup.max_severity] -
        SEVERITY_RANK[a.rollup.max_severity];
      if (severity !== 0) return severity;
      if (b.rollup.actionable !== a.rollup.actionable) {
        return b.rollup.actionable - a.rollup.actionable;
      }
      return a.label.localeCompare(b.label);
    });
}
