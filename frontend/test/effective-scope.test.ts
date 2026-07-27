import { describe, expect, it } from "vitest";
import {
  effectiveScopeState,
  isScopeItemEffectivelyIncluded,
} from "@/lib/effective-scope";
import type { Entity, ScopeItem } from "@/lib/types";

function scopeItem(overrides: Partial<ScopeItem> = {}): ScopeItem {
  return {
    id: "scope-1",
    engagement_id: "eng-1",
    kind: "domain",
    value: "example.com",
    is_exclusion: false,
    note: null,
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

it("uses the structured server projection before compatibility fields", () => {
  const item = scopeItem({
    is_effectively_in_scope: true,
    effective_scope: {
      state: "excluded",
      allowed: false,
      reason_code: "excluded_domain",
      reason: "matched parent exclusion",
      target: "example.com",
      target_kind: "domain",
      matched_include_id: null,
      matched_exclusion_id: "scope-2",
    },
  });
  expect(isScopeItemEffectivelyIncluded(item)).toBe(false);
});

describe("effectiveScopeState", () => {
  it("uses server state and retains legacy fallback", () => {
    const projected = {
      scope_status: "oos",
      effective_scope: {
        state: "included",
        allowed: true,
        reason_code: "included_parent_domain",
        reason: "matched parent",
        target: "app.example.com",
        target_kind: "domain",
        matched_include_id: "scope-1",
        matched_exclusion_id: null,
      },
    } satisfies Pick<Entity, "scope_status" | "effective_scope">;
    expect(effectiveScopeState(projected)).toBe("included");
    expect(effectiveScopeState({ scope_status: "excluded" })).toBe("excluded");
    expect(effectiveScopeState({ scope_status: "legacy" })).toBe("unmatched");
  });
});
