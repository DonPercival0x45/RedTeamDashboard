import type { PlaybookCategory, PlaybookRead } from "@/lib/types";

export const PLAYBOOK_CATEGORY_ORDER: PlaybookCategory[] = [
  "discovery",
  "enumeration",
  "posture",
  "exposure",
  "validation",
  "scope_review",
  "other",
];

export const PLAYBOOK_CATEGORY_LABEL: Record<PlaybookCategory, string> = {
  discovery: "Discovery",
  enumeration: "Enumeration",
  posture: "Security posture",
  exposure: "Exposure",
  validation: "Validation",
  scope_review: "Scope review",
  other: "Other",
};

function entityFamily(value: string): string {
  return value === "domain" || value === "subdomain" || value === "host"
    ? "domain"
    : value;
}

export function playbookEntityTypes(playbook: PlaybookRead): string[] {
  return playbook.applicable_entity_types?.length
    ? playbook.applicable_entity_types
    : [playbook.applies_to_asset_class];
}

export function isPlaybookApplicable(
  playbook: PlaybookRead,
  entityType: string,
): boolean {
  const types = playbookEntityTypes(playbook);
  if (types.includes("scope")) return true;
  const family = entityFamily(entityType);
  return types.some(
    (type) => type === entityType || entityFamily(type) === family,
  );
}

export type PlaybookSort = "recommended" | "name" | "steps_desc" | "steps_asc";

export function sortPlaybooks(
  playbooks: PlaybookRead[],
  sort: PlaybookSort,
  entityType?: string | null,
): PlaybookRead[] {
  const collator = new Intl.Collator(undefined, {
    numeric: true,
    sensitivity: "base",
  });
  return [...playbooks].sort((left, right) => {
    if (sort === "recommended" && entityType) {
      const applicability =
        Number(isPlaybookApplicable(right, entityType)) -
        Number(isPlaybookApplicable(left, entityType));
      if (applicability) return applicability;
      const rank = (playbook: PlaybookRead) => {
        const types = playbookEntityTypes(playbook);
        if (types.includes(entityType)) return 2;
        if (types.includes("scope")) return 0;
        return types.some((type) => entityFamily(type) === entityFamily(entityType))
          ? 1
          : 0;
      };
      const matchRank = rank(right) - rank(left);
      if (matchRank) return matchRank;
    }
    if (sort === "steps_desc" && left.step_count !== right.step_count) {
      return right.step_count - left.step_count;
    }
    if (sort === "steps_asc" && left.step_count !== right.step_count) {
      return left.step_count - right.step_count;
    }
    const byName = collator.compare(left.name, right.name);
    if (byName) return byName;
    const bySlug = collator.compare(left.slug, right.slug);
    if (bySlug) return bySlug;
    if (left.version !== right.version) return right.version - left.version;
    return collator.compare(left.id, right.id);
  });
}
