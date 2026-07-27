import type { Entity, ScopeItem, ScopeKind } from "@/lib/types";

export function entityKey(entity: Pick<Entity, "type" | "value">): string {
  return `${entity.type}\u0000${entity.value}`;
}

function looksLikeIp(value: string): boolean {
  const trimmed = value.trim();
  if (trimmed.includes(":")) return true;
  const parts = trimmed.split(".");
  return (
    parts.length === 4 &&
    parts.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255)
  );
}

export function scopeTargetForEntity(
  entity: Pick<Entity, "type" | "value">,
): { kind: ScopeKind; value: string } | null {
  const value = entity.value.trim();
  if (!value || value.includes("\n") || value.includes("\r")) return null;
  switch (entity.type) {
    case "ip":
      return { kind: "ip", value };
    case "cidr":
      return { kind: "cidr", value };
    case "domain":
    case "subdomain":
      return { kind: "domain", value };
    case "url":
      return { kind: "url", value };
    case "email":
      return { kind: "email", value };
    case "host":
      return { kind: looksLikeIp(value) ? "ip" : "domain", value };
    default:
      return null;
  }
}

function comparableScopeValue(kind: ScopeKind, value: string): string {
  const trimmed = value.trim();
  if (kind === "domain") return trimmed.toLowerCase().replace(/\.+$/, "");
  if (kind === "email") {
    const separator = trimmed.lastIndexOf("@");
    if (separator > 0 && separator < trimmed.length - 1) {
      return `${trimmed.slice(0, separator)}@${trimmed
        .slice(separator + 1)
        .toLowerCase()
        .replace(/\.+$/, "")}`;
    }
  }
  return trimmed;
}

export function exactScopeRules(
  entity: Pick<Entity, "type" | "value">,
  items: ScopeItem[],
): ScopeItem[] {
  const target = scopeTargetForEntity(entity);
  if (!target) return [];
  const value = comparableScopeValue(target.kind, target.value);
  return items.filter(
    (item) =>
      item.kind === target.kind &&
      comparableScopeValue(item.kind, item.value) === value,
  );
}

export function scopeActionState(
  entity: Pick<Entity, "type" | "value" | "scope_status">,
  items: ScopeItem[],
) {
  const rules = exactScopeRules(entity, items);
  const exactIncludes = rules.filter((item) => !item.is_exclusion);
  const exactExclusions = rules.filter((item) => item.is_exclusion);
  const hasExactRule = exactIncludes.length > 0 || exactExclusions.length > 0;
  return {
    rules,
    exactIncludes,
    exactExclusions,
    // Never hide two mutations behind one button. Existing exact rules must be
    // explicitly removed before the opposite disposition can be created.
    canAdd: !hasExactRule && entity.scope_status !== "live",
    canExclude: !hasExactRule,
    isIncluded: exactIncludes.length > 0 || entity.scope_status === "live",
    isExcluded: exactExclusions.length > 0,
  };
}
