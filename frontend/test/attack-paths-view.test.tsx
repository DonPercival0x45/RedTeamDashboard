import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AttackPathsView } from "@/components/attack-paths-view";

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
    ],
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

describe("AttackPathsView", () => {
  it("shows observed citations, validation filtering, and entity drilldowns", async () => {
    const user = userEvent.setup();
    render(<AttackPathsView slug="example" />);

    expect(screen.getByText("Attack paths")).toBeInTheDocument();
    expect(screen.getByText("Evidence paths")).toBeInTheDocument();
    expect(screen.getAllByText("Observed").length).toBeGreaterThan(0);
    expect(screen.getByText(/do not establish ownership, scope/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /192.0.2.10/ })).toHaveAttribute(
      "href",
      expect.stringContaining("view=entities"),
    );
    await user.click(screen.getByRole("button", { name: /Needs review/ }));
    expect(screen.getAllByText("DNS path · needs review").length).toBeGreaterThan(0);
  });
});
