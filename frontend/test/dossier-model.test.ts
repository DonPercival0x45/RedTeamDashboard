import { describe, expect, it } from "vitest";
import {
  buildDossierTimeline,
  extractDossierRelationships,
} from "@/lib/dossier";
import type { Finding, Observation, PlaybookRunRead } from "@/lib/types";

function finding(overrides: Partial<Finding> = {}): Finding {
  return {
    id: "finding-1",
    thread_id: null,
    tool: "dns_lookup",
    target: "akam.net",
    args: {},
    data: {},
    severity: "info",
    title: "Subdomains discovered — akam.net",
    phase: "osint",
    status: "validated",
    validated_at: null,
    observed_at: null,
    burp_serial_number: null,
    created_at: "2026-07-25T02:13:42.000Z",
    ...overrides,
  };
}

describe("Dossier relationship projection", () => {
  it("preserves the exact structured DNS path and item observation time", () => {
    const relationships = extractDossierRelationships([
      finding({
        data: {
          items: [
            {
              subdomain: "a8-67.akam.net",
              a: ["2.16.40.67"],
              cname: [],
              first_seen_at: "2026-07-25T02:14:39.563Z",
            },
          ],
        },
      }),
    ]);

    expect(relationships).toHaveLength(1);
    expect(relationships[0]).toMatchObject({
      sourceType: "subdomain",
      sourceValue: "a8-67.akam.net",
      targetType: "ip",
      targetValue: "2.16.40.67",
      kind: "resolves_to",
      observedAt: "2026-07-25T02:14:39.563Z",
      findingId: "finding-1",
    });
  });

  it("retains item-level DNS provenance in unified grouped findings", () => {
    const relationships = extractDossierRelationships([
      finding({
        tool: "subfinder",
        target: "akam.net",
        data: {
          items: [
            {
              type: "A",
              value: "2.16.40.67",
              source_tool: "dns_lookup",
              first_seen_at: "2026-07-25T02:14:39.563Z",
            },
          ],
        },
      }),
    ]);

    expect(relationships[0]).toMatchObject({
      sourceType: "domain",
      sourceValue: "akam.net",
      targetType: "ip",
      targetValue: "2.16.40.67",
      kind: "resolves_to",
    });
  });

  it("does not invent edges from prose or non-DNS records with similar keys", () => {
    const relationships = extractDossierRelationships([
      finding({
        summary: "Perhaps example.test points at 203.0.113.10",
        data: { notes: "example.test may use 203.0.113.10" },
      }),
      finding({
        id: "not-dns",
        tool: "asset_import",
        data: {
          items: [{ host: "example.test", a: ["203.0.113.10"] }],
        },
      }),
    ]);

    expect(relationships).toEqual([]);
  });
});

describe("Dossier timeline", () => {
  it("labels generic findings as records rather than verified observations", () => {
    const manual = finding({
      id: "manual-1",
      tool: null,
      target: "203.0.113.8",
      title: "Possible exposed service",
      status: "needs_review",
    });

    const timeline = buildDossierTimeline([manual], [], [], []);

    expect(timeline[0]).toMatchObject({
      id: "finding:manual-1",
      trust: "record",
      sourceLabel: "Finding record",
    });
    expect(timeline[0].description).toContain("needs review");
  });

  it("combines cited relationships, analyst notes, and execution outcomes", () => {
    const dnsFinding = finding({
      data: {
        items: [
          {
            subdomain: "a8-67.akam.net",
            a: ["2.16.40.67"],
            first_seen_at: "2026-07-25T02:14:39.563Z",
          },
        ],
      },
    });
    const relationships = extractDossierRelationships([dnsFinding]);
    const observations: Observation[] = [
      {
        id: "observation-1",
        content: "Likely third-party DNS infrastructure; confirm before scoping.",
        phase: "osint",
        created_by: "analyst@example.test",
        created_at: "2026-07-25T03:00:00.000Z",
        finding_ids: [dnsFinding.id],
      },
    ];
    const runs = [
      {
        id: "run-1",
        playbook_slug: "ip-intelligence-and-ownership",
        status: "partial",
        completed_at: "2026-07-25T04:00:00.000Z",
        started_at: "2026-07-25T03:55:00.000Z",
        steps_succeeded: 1,
        steps_failed: 2,
        findings_new: 0,
      } as PlaybookRunRead,
    ];

    const timeline = buildDossierTimeline(
      [dnsFinding],
      observations,
      runs,
      relationships,
    );

    expect(timeline.map((row) => row.kind)).toEqual([
      "run",
      "observation",
      "relationship",
    ]);
    expect(timeline.find((row) => row.kind === "relationship")).toMatchObject({
      findingId: "finding-1",
      entityType: "ip",
      entityValue: "2.16.40.67",
      trust: "observed",
    });
    expect(timeline.some((row) => row.id === "finding:finding-1")).toBe(false);
  });
});
