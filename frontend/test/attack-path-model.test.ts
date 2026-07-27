import { describe, expect, it } from "vitest";
import {
  buildAttackPathEdges,
  buildAttackPaths,
  buildScopeAwareAttackPathProjection,
} from "@/lib/attack-paths";
import type { Entity, Finding } from "@/lib/types";

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

  it("moves explicitly excluded provider branches out of the active projection", () => {
    const provider = dnsFinding("provider", "domaincontrol.com", [
      {
        domain: "domaincontrol.com",
        type: "CNAME",
        value: "dnsmgdp05.nw1.pods.domaincontrol.com",
      },
    ]);
    const active = dnsFinding("active", "app.example", [
      { domain: "app.example", type: "A", value: "192.0.2.10" },
    ]);
    const entities: Entity[] = [
      {
        type: "domain",
        value: "domaincontrol.com",
        count: 1,
        severity: "info",
        first_seen: "2026-07-25T10:00:00Z",
        last_seen: "2026-07-25T10:00:00Z",
        findings: [],
        scope_status: "excluded",
        relevance: "excluded",
      },
    ];

    const projection = buildScopeAwareAttackPathProjection(
      [provider, active],
      entities,
      4,
      1,
    );
    expect(projection.paths).toHaveLength(2);
    const activePath = projection.paths.find((path) => !path.outOfScope);
    expect(activePath?.nodes[0].value).toBe("app.example");
    expect(projection.paths.filter((path) => path.outOfScope)).toHaveLength(1);

    const all = buildScopeAwareAttackPathProjection(
      [provider, active],
      entities,
      4,
      10,
    );
    const excluded = all.paths.find((path) =>
      path.nodes.some((node) => node.value === "domaincontrol.com"),
    );
    expect(excluded?.outOfScope).toBe(true);
    expect(excluded?.excludedEntityCount).toBe(1);
  });

  it("does not let an active first hop into a broad excluded branch starve later active roots", () => {
    const firstHop = dnsFinding("first", "a.example", [
      { domain: "a.example", type: "CNAME", value: "relay.example" },
    ]);
    const providerHop = dnsFinding("provider-hop", "relay.example", [
      {
        domain: "relay.example",
        type: "CNAME",
        value: "provider.shared.example",
      },
    ]);
    const laterActive = dnsFinding("later", "z.example", [
      { domain: "z.example", type: "A", value: "192.0.2.30" },
    ]);
    const entities: Entity[] = [
      {
        type: "domain",
        value: "provider.shared.example",
        count: 1,
        severity: "info",
        first_seen: "2026-07-25T10:00:00Z",
        last_seen: "2026-07-25T10:00:00Z",
        findings: [],
        scope_status: "excluded",
      },
    ];
    const projection = buildScopeAwareAttackPathProjection(
      [firstHop, providerHop, laterActive],
      entities,
      4,
      1,
    );
    expect(projection.paths.some((path) => !path.outOfScope)).toBe(true);
    expect(
      projection.paths.find((path) => !path.outOfScope)?.nodes[0].value,
    ).toBe("z.example");
  });

  it("treats an edge as out of scope only when all supporting findings are excluded", () => {
    const excluded = dnsFinding("excluded", "provider.example", [
      { domain: "provider.example", type: "A", value: "192.0.2.20" },
    ]);
    excluded.exclusion = "out_of_scope";
    const reportable = dnsFinding("reportable", "provider.example", [
      { domain: "provider.example", type: "A", value: "192.0.2.20" },
    ]);
    expect(buildAttackPathEdges([excluded])[0].outOfScope).toBe(true);
    expect(buildAttackPathEdges([excluded, reportable])[0].outOfScope).toBe(false);
  });

  it("does not infer a path from narrative text", () => {
    const finding = dnsFinding("a", "example.com", []);
    finding.summary = "example.com resolves to 192.0.2.1";
    finding.data = { prose: "example.com resolves to 192.0.2.1" };
    expect(buildAttackPaths([finding])).toEqual([]);
  });
});
