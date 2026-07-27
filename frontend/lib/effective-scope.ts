import type { Entity, EffectiveScopeState, ScopeItem } from "@/lib/types";

/** Server projection is authoritative; legacy fields are compatibility only. */
export function effectiveScopeState(
  value: Pick<Entity, "scope_status" | "effective_scope">,
): EffectiveScopeState {
  if (value.effective_scope) return value.effective_scope.state;
  if (value.scope_status === "live") return "included";
  if (value.scope_status === "excluded") return "excluded";
  return "unmatched";
}

export function isScopeItemEffectivelyIncluded(item: ScopeItem): boolean {
  if (item.is_exclusion) return false;
  if (item.effective_scope) {
    return (
      item.effective_scope.state === "included" &&
      item.effective_scope.allowed
    );
  }
  return item.is_effectively_in_scope !== false;
}
