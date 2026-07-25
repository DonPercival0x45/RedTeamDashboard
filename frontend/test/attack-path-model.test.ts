import { describe, expect, it } from "vitest";
import { buildAttackPathEdges, buildAttackPaths } from "@/lib/attack-paths";
import type { Finding } from "@/lib/types";

function dnsFinding(
  id: string,
  target: string,
  items: Record<string, unknown>[],
  status: Finding["status"] = "validated",
): Finding {
  return {
    id,
    thread_id: null,
    tool: "dns_inventory",
    target,
    args: {},
    data: { items },
    severity: "medium",
    title: `DNS ${target}`,
    phase: "osint",
    status,
    validated_at: null,
    observed_at: "2026-07-25T10:00:00Z",
    burp_serial_number: null,
    created_at: "2026-07-25T10:00:00Z",
  };
}

describe("attack path projection", () => {
  it("builds stable observed chains from structured evidence", () => {
    const findings = [
      dnsFinding("2", "edge.example", [
        { domain: "edge.example", type: "CNAME", value: "origin.example" },
      ]),
      dnsFinding("1", "origin.example", [
        { domain: "origin.example", type: "A", value: "192.0.2.10" },
      ]),
    ];
    const paths = buildAttackPaths(findings);

    expect(paths).toHaveLength(1);
    expect(paths[0].nodes.map((node) => node.value)).toEqual([
      "edge.example",
      "origin.example",
      "192.0.2.10",
    ]);
    expect(paths[0].edges.map((edge) => edge.relation)).toEqual([
      "aliases_to",
      "resolves_to",
    ]);
    expect(buildAttackPaths([...findings].reverse())).toEqual(paths);
  });

  it("consolidates repeated edges while preserving citations and validation state", () => {
    const findings = [
      dnsFinding("a", "example.com", [{ domain: "example.com", a: ["192.0.2.1"] }]),
      dnsFinding(
        "b",
        "example.com",
        [{ domain: "example.com", a: ["192.0.2.1"] }],
        "needs_review",
      ),
    ];

    const edges = buildAttackPathEdges(findings);
    expect(edges).toHaveLength(1);
    expect(edges[0].citations.map((citation) => citation.findingId).sort()).toEqual(["a", "b"]);
    expect(edges[0].needsValidation).toBe(true);
  });

  it("marks invalidated-only evidence as disputed and removes its severity", () => {
    const rejected = dnsFinding(
      "rejected",
      "example.com",
      [{ domain: "example.com", a: ["192.0.2.2"] }],
      "rejected",
    );
    rejected.severity = "critical";
    const edge = buildAttackPathEdges([rejected])[0];
    expect(edge.disputed).toBe(true);
    expect(edge.maxSeverity).toBe("info");
  });

  it("uses a stable representation and stops paths before a node cycle", () => {
    const upper = dnsFinding("upper", "Example.COM", [
      { domain: "Example.COM", type: "CNAME", value: "Alias.EXAMPLE" },
    ]);
    const lower = dnsFinding("lower", "example.com", [
      { domain: "example.com", type: "CNAME", value: "alias.example" },
      { domain: "alias.example", type: "CNAME", value: "example.com" },
    ]);
    expect(buildAttackPathEdges([upper, lower])).toEqual(
      buildAttackPathEdges([lower, upper]),
    );
    const paths = buildAttackPaths([upper, lower]);
    expect(paths.every((path) => new Set(path.nodes.map((node) => node.value.toLowerCase())).size === path.nodes.length)).toBe(true);
  });

  it("does not infer a path from narrative text", () => {
    const finding = dnsFinding("a", "example.com", []);
    finding.summary = "example.com resolves to 192.0.2.1";
    finding.data = { prose: "example.com resolves to 192.0.2.1" };
    expect(buildAttackPaths([finding])).toEqual([]);
  });
});
