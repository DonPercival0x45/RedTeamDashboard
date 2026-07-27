import { describe, expect, it } from "vitest";

import { filterHierarchy } from "@/lib/finding-hierarchy";
import type { FindingHierarchyItem } from "@/lib/types";

const baseRollup = {
  max_severity: "high" as const,
  needs_review: 1,
  actionable: 1,
  inventory: 1,
  resolved_excluded: 0,
  distinct_findings: 3,
  latest_at: "2026-07-27T00:00:00Z",
};

const item: FindingHierarchyItem = {
  id: "asset",
  kind: "ip",
  canonical_key: "ip:192.0.2.10",
  label: "Service Detection: IP(192.0.2.10)",
  value: "192.0.2.10",
  ip: "192.0.2.10",
  hostname: null,
  protocol: null,
  port: null,
  service: null,
  url: null,
  rollup: baseRollup,
  create_finding_allowed: true,
  suggested_title: "Finding on IP",
  suggested_target: "192.0.2.10",
  finding_refs: [],
  children: [
    {
      id: "service",
      kind: "service",
      canonical_key: "ip:192.0.2.10:service:tcp:443",
      label: "443/tcp · HTTPS · nginx 1.24",
      value: "192.0.2.10:443/tcp",
      ip: "192.0.2.10",
      hostname: null,
      protocol: "tcp",
      port: 443,
      service: "https",
      url: null,
      rollup: baseRollup,
      create_finding_allowed: true,
      suggested_title: "HTTPS exposure",
      suggested_target: "192.0.2.10:443",
      children: [],
      finding_refs: [
        {
          id: "pending-id",
          title: "Changed HTTPS fingerprint",
          tool: "service_detect",
          target: "192.0.2.10:443",
          severity: "high",
          phase: "vuln_scan",
          status: "needs_review",
          exclusion: null,
          observed_at: null,
          created_at: "2026-07-27T00:00:00Z",
          bucket: "needs_review",
        },
        {
          id: "actionable-id",
          title: "Exposed console",
          tool: "manual_promotion",
          target: "192.0.2.10:443",
          severity: "critical",
          phase: "vuln_scan",
          status: "validated",
          exclusion: null,
          observed_at: null,
          created_at: "2026-07-26T00:00:00Z",
          bucket: "actionable",
        },
        {
          id: "inventory-id",
          title: "Port observed",
          tool: "portscan",
          target: "192.0.2.10:443",
          severity: "info",
          phase: "osint",
          status: "validated",
          exclusion: null,
          observed_at: null,
          created_at: "2026-07-25T00:00:00Z",
          bucket: "inventory",
        },
      ],
    },
  ],
};

describe("finding hierarchy view model", () => {
  it("keeps ancestors while filtering each workspace view", () => {
    const focus = filterHierarchy([item], "focus");
    expect(focus).toHaveLength(1);
    expect(focus[0].children[0].finding_refs.map((ref) => ref.id)).toEqual([
      "pending-id",
      "actionable-id",
    ]);
    const inventory = filterHierarchy([item], "inventory")[0];
    expect(inventory.children[0].finding_refs).toHaveLength(1);
    expect(inventory.rollup.max_severity).toBe("info");
    expect(inventory.rollup.actionable).toBe(0);
    expect(inventory.rollup.needs_review).toBe(0);
  });

  it("searches nested services while retaining their IP parent", () => {
    const matches = filterHierarchy([item], "focus", "nginx");
    expect(matches).toHaveLength(1);
    expect(matches[0].label).toContain("192.0.2.10");
    expect(matches[0].children[0].port).toBe(443);
    expect(filterHierarchy([item], "focus", "smtp")).toEqual([]);
  });
});
