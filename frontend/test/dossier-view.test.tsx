import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const hookState = vi.hoisted(() => ({
  entitiesError: null as Error | null,
}));

vi.mock("@/lib/hooks", () => ({
  useEngagement: () => ({
    data: { name: "Example engagement", description: "External assessment." },
    isLoading: false,
    error: null,
  }),
  useFindings: () => ({
    data: [
      {
        id: "finding-1",
        thread_id: null,
        tool: "dns_lookup",
        target: "akam.net",
        args: {},
        data: {
          items: [
            {
              subdomain: "a8-67.akam.net",
              a: ["2.16.40.67"],
              first_seen_at: "2026-07-25T02:14:39.563Z",
            },
          ],
        },
        severity: "info",
        title: "Subdomains discovered — akam.net",
        phase: "osint",
        status: "validated",
        validated_at: null,
        observed_at: null,
        burp_serial_number: null,
        created_at: "2026-07-25T02:13:42.000Z",
      },
    ],
    error: null,
    isLoading: false,
  }),
  useEntities: () => ({
    data: [
      {
        type: "ip",
        value: "2.16.40.67",
        count: 3,
        severity: "info",
        first_seen: "2026-07-25T02:14:39.563Z",
        last_seen: "2026-07-25T18:43:16.000Z",
        findings: [],
        scope_status: "oos",
        relevance: "review",
      },
    ],
    isLoading: false,
    error: hookState.entitiesError,
  }),
  useStoredEntities: () => ({ data: [], isLoading: false, error: null }),
  useObservations: () => ({ data: [], isLoading: false, error: null }),
  usePlaybookRuns: () => ({ data: [], isLoading: false, error: null }),
}));

import { DossierView } from "@/components/dossier-view";

afterEach(() => {
  hookState.entitiesError = null;
});

describe("DossierView narrative", () => {
  it("keeps evidence-backed storytelling and exact DNS provenance together", () => {
    render(<DossierView slug="example" />);

    expect(screen.getByText("Engagement dossier")).toBeInTheDocument();
    expect(screen.getByText("Current picture")).toBeInTheDocument();
    expect(screen.getAllByText("a8-67.akam.net").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2.16.40.67").length).toBeGreaterThan(0);
    expect(screen.getByText("How the infrastructure connects")).toBeInTheDocument();
    expect(screen.getByText("Engagement timeline")).toBeInTheDocument();
    expect(screen.getByText("Research gaps")).toBeInTheDocument();
    expect(
      screen.getByText(/do not establish ownership or authorization/i),
    ).toBeInTheDocument();
  });

  it("withholds factual counts and gap conclusions when a source query fails", () => {
    hookState.entitiesError = new Error("entity projection unavailable");
    render(<DossierView slug="example" />);

    expect(screen.getByRole("alert")).toHaveTextContent("The dossier is incomplete");
    expect(screen.queryByText("Current picture")).not.toBeInTheDocument();
    expect(screen.queryByText("No immediate dossier gaps were detected")).not.toBeInTheDocument();
  });
});
