import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const hookState = vi.hoisted(() => ({
  entitiesError: null as Error | null,
}));

vi.mock("@/lib/hooks", () => ({
  qk: {
    scope: (slug: string) => ["scope", slug],
    entities: (slug: string) => ["entities", slug],
    storedEntities: (slug: string) => ["stored-entities", slug],
    engagements: () => ["engagements"],
  },
  useEngagement: () => ({
    data: { name: "Example engagement", description: "External assessment." },
    isLoading: false,
    error: null,
  }),
  useFindings: () => ({
    data: [
      {
        id: "pending-excluded",
        thread_id: null,
        tool: "dns_lookup",
        target: "excluded.example",
        args: {},
        data: {},
        severity: "critical",
        title: "Excluded evidence",
        phase: "vuln_scan",
        status: "pending_validation",
        validated_at: null,
        observed_at: null,
        burp_serial_number: null,
        created_at: "2026-07-25T02:15:00.000Z",
      },
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
      {
        type: "domain",
        value: "excluded.example",
        count: 12,
        severity: "critical",
        first_seen: "2026-07-25T00:00:00.000Z",
        last_seen: "2026-07-25T20:00:00.000Z",
        findings: [{ id: "pending-excluded", title: "Excluded evidence", tool: "dns_lookup", severity: "critical", phase: "osint" }],
        scope_status: "excluded",
        relevance: "excluded",
        review_disposition: "excluded",
      },
      {
        type: "domain",
        value: "validated.example",
        count: 8,
        severity: "critical",
        first_seen: "2026-07-25T01:00:00.000Z",
        last_seen: "2026-07-25T19:00:00.000Z",
        findings: [],
        scope_status: "live",
        relevance: "in_scope",
      },
    ],
    isLoading: false,
    error: hookState.entitiesError,
  }),
  useStoredEntities: () => ({ data: [], isLoading: false, error: null }),
  useScope: () => ({ data: [], isLoading: false, error: null }),
  useMe: () => ({ data: { role: "user" } }),
  useObservations: () => ({ data: [], isLoading: false, error: null }),
  usePlaybookRuns: () => ({ data: [], isLoading: false, error: null }),
}));

import { DossierView } from "@/components/dossier-view";

function renderDossier() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <DossierView slug="example" />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  hookState.entitiesError = null;
});

describe("DossierView narrative", () => {
  it("uses strategy-style tabs while keeping exact DNS provenance", async () => {
    const user = userEvent.setup();
    renderDossier();

    expect(screen.getByText("Engagement dossier")).toBeInTheDocument();
    expect(screen.getByText("Current picture")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Entity review/ })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Relationships" }));
    expect(screen.getAllByText("a8-67.akam.net").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2.16.40.67").length).toBeGreaterThan(0);
    expect(screen.getByText("How the infrastructure connects")).toBeInTheDocument();
    expect(
      screen.getByText(/path links back to the finding/i),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Entity review/ }));
    expect(screen.getByText("Entity validation queue")).toBeInTheDocument();
    expect(screen.getAllByText("Needs validation").length).toBeGreaterThan(0);
    expect(screen.getByText("2.16.40.67")).toBeInTheDocument();
    expect(screen.queryByText("validated.example")).not.toBeInTheDocument();
    expect(screen.queryByText("excluded.example")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "All (3)" }));
    const entityValues = screen
      .getAllByText(/2\.16\.40\.67|validated\.example|excluded\.example/)
      .map((node) => node.textContent);
    expect(entityValues).toEqual([
      "2.16.40.67",
      "excluded.example",
      "validated.example",
    ]);
    await user.click(screen.getByRole("button", { name: "Select all matching (3)" }));
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Open ip 2.16.40.67" }),
    );
    expect(screen.getByRole("dialog", { name: "2.16.40.67" })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("tab", { name: "Timeline" }));
    expect(screen.getByText("Engagement timeline")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Research gaps" }));
    expect(screen.getAllByText("Research gaps").length).toBeGreaterThan(1);
  });

  it("withholds factual counts and gap conclusions when a source query fails", () => {
    hookState.entitiesError = new Error("entity projection unavailable");
    renderDossier();

    expect(screen.getByRole("alert")).toHaveTextContent("The dossier is incomplete");
    expect(screen.queryByText("Current picture")).not.toBeInTheDocument();
    expect(screen.queryByText("No immediate dossier gaps were detected")).not.toBeInTheDocument();
  });
});
