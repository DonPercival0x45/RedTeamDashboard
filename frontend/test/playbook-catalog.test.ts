import { describe, expect, it } from "vitest";
import {
  isPlaybookApplicable,
  sortPlaybooks,
} from "@/lib/playbook-catalog";
import type { PlaybookRead } from "@/lib/types";

function playbook(
  id: string,
  name: string,
  types: string[],
  steps = 1,
): PlaybookRead {
  return {
    id,
    slug: id,
    version: 1,
    name,
    description: null,
    applies_to_asset_class: types[0],
    applicable_entity_types: types,
    active: false,
    step_count: steps,
    required_executor: "internal",
  };
}

describe("playbook catalog model", () => {
  it("matches entity families and generic scope recipes", () => {
    expect(isPlaybookApplicable(playbook("d", "Domain", ["domain"]), "subdomain")).toBe(true);
    expect(isPlaybookApplicable(playbook("s", "Scope", ["scope"]), "email")).toBe(true);
    expect(isPlaybookApplicable(playbook("i", "IP", ["ip"]), "domain")).toBe(false);
  });

  it("sorts deterministically with exact matches before family and scope", () => {
    const rows = [
      playbook("scope", "Z scope", ["scope"]),
      playbook("family", "A domain", ["domain"]),
      playbook("exact", "B subdomain", ["subdomain"]),
    ];
    expect(sortPlaybooks(rows, "recommended", "subdomain").map((row) => row.id)).toEqual([
      "exact",
      "family",
      "scope",
    ]);
    expect(sortPlaybooks(rows, "name").map((row) => row.id)).toEqual([
      "family",
      "exact",
      "scope",
    ]);
  });
});
