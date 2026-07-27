import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AttackPathsView } from "@/components/attack-paths-view";

const hookState = vi.hoisted(() => ({
  entitiesError: null as Error | null,
}));

vi.mock("@/lib/hooks", () => ({
  useFindings: () => ({
    data: [
      {
        id: "finding-1",
        thread_id: null,
        tool: "dns_inventory",
        target: "app.example",
        args: {},
        data: {
          items: [
            { domain: "app.example", type: "CNAME", value: "edge.example" },
            { domain: "edge.example", type: "A", value: "192.0.2.10" },
          ],
        },
        severity: "high",
        title: "DNS path",
        phase: "osint",
        status: "needs_review",
        validated_at: null,
        observed_at: "2026-07-25T10:00:00Z",
        burp_serial_number: null,
        created_at: "2026-07-25T10:00:00Z",
      },
      {
        id: "finding-provider",
        thread_id: null,
        tool: "dns_inventory",
        target: "domaincontrol.com",
        args: {},
        data: {
          items: [
            {
              domain: "domaincontrol.com",
              type: "CNAME",
              value: "dnsmgdp05.nw1.pods.domaincontrol.com",
            },
          ],
        },
        severity: "info",
        title: "Shared DNS provider path",
        phase: "osint",
        status: "validated",
        validated_at: null,
        observed_at: "2026-07-25T10:00:00Z",
        burp_serial_number: null,
        created_at: "2026-07-25T10:00:00Z",
      },
    ],
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useEntities: () => ({
    data: [
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
    ],
    isLoading: false,
    isFetching: false,
    error: hookState.entitiesError,
    refetch: vi.fn(),
  }),
}));

afterEach(() => {
  hookState.entitiesError = null;
});

describe("AttackPathsView", () => {
  it("shows observed citations, validation filtering, and entity drilldowns", async () => {
    const user = userEvent.setup();
    render(<AttackPathsView slug="example" />);

    expect(screen.getByText("Attack paths")).toBeInTheDocument();
    expect(screen.getByText("Active evidence paths")).toBeInTheDocument();
    expect(screen.getAllByText("Observed").length).toBeGreaterThan(0);
    expect(screen.getByText(/do not establish ownership, scope/i)).toBeInTheDocument();
    expect(screen.queryByText("domaincontrol.com")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /192.0.2.10/ })).toHaveAttribute(
      "href",
      expect.stringContaining("view=entities"),
    );
    await user.click(screen.getByRole("button", { name: /Needs review/ }));
    expect(screen.getAllByText("DNS path · needs review").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: /Out of scope \(1\)/ }));
    expect(screen.getAllByText("domaincontrol.com").length).toBeGreaterThan(0);
    expect(screen.getByText("Shared DNS provider path · validated")).toBeInTheDocument();
    expect(screen.getAllByText("Out of scope").length).toBeGreaterThan(0);
  });

  it("warns when cached entity scope cannot be refreshed", () => {
    hookState.entitiesError = new Error("entity scope refresh failed");
    render(<AttackPathsView slug="example" />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Refresh failed; showing cached data.",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "entity scope refresh failed",
    );
  });
});
