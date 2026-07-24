import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useDecisionInbox = vi.fn();

vi.mock("@/lib/hooks", () => ({
  useDecisionInbox: () => useDecisionInbox(),
}));

vi.mock("@/components/approvals-modal", () => ({
  ApprovalsModal: ({ pending }: { pending: { tool: string } | null }) =>
    pending ? <div>Tool decision: {pending.tool}</div> : null,
}));

vi.mock("@/components/playbooks/run-detail-modal", () => ({
  RunDetailModal: ({ runId }: { runId: string }) => (
    <div>Playbook decision: {runId}</div>
  ),
}));

import { ApprovalInbox } from "@/components/approval-inbox";

const decisions = [
  {
    kind: "tool_approval",
    id: "approval-1",
    engagement_id: "eng-1",
    engagement_slug: "alpha",
    engagement_name: "Alpha",
    thread_id: "1234567890abcdef",
    node: null,
    tool_name: "nmap",
    tool_args: { target: "foo.example" },
    risk: "active",
    scope_check: {},
    status: "pending",
    decided_by: null,
    decision_args: null,
    authorization_id: null,
    decided_at: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  {
    kind: "playbook_run",
    id: "run-1",
    engagement_id: "eng-1",
    engagement_slug: "alpha",
    engagement_name: "Alpha",
    created_at: new Date().toISOString(),
    playbook_slug: "dns-passive",
    playbook_name: "Passive DNS",
    playbook_version: 1,
    executor: "internal",
    scope_subset: ["foo.example"],
    requested_by: "user-1",
  },
];

describe("ApprovalInbox", () => {
  beforeEach(() => {
    useDecisionInbox.mockReset();
    useDecisionInbox.mockReturnValue({
      data: decisions,
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    });
  });

  it("counts and renders both tool and playbook decisions", () => {
    render(<ApprovalInbox variant="sidebar" />);
    fireEvent.click(screen.getByRole("button", { name: /2 pending decisions/i }));

    expect(screen.getByText("Pending decisions")).toBeInTheDocument();
    expect(screen.getByText("nmap")).toBeInTheDocument();
    expect(screen.getByText("Passive DNS")).toBeInTheDocument();
  });

  it("opens the existing playbook decision modal for a gated run", () => {
    render(<ApprovalInbox variant="sidebar" />);
    fireEvent.click(screen.getByRole("button", { name: /2 pending decisions/i }));
    fireEvent.click(screen.getByRole("button", { name: /Passive DNS/i }));

    expect(screen.getByText("Playbook decision: run-1")).toBeInTheDocument();
  });

  it("keeps legacy tool approvals on their existing decision modal", () => {
    render(<ApprovalInbox variant="sidebar" />);
    fireEvent.click(screen.getByRole("button", { name: /2 pending decisions/i }));
    fireEvent.click(screen.getByRole("button", { name: /nmap/i }));

    expect(screen.getByText("Tool decision: nmap")).toBeInTheDocument();
  });

  it("keeps cached decisions actionable when a background refresh fails", () => {
    useDecisionInbox.mockReturnValue({
      data: decisions,
      error: new Error("timeout"),
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    });
    render(<ApprovalInbox variant="sidebar" />);
    fireEvent.click(screen.getByRole("button", { name: /2 pending decisions/i }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Refresh failed; showing cached data.",
    );
    expect(screen.getByText("Passive DNS")).toBeInTheDocument();
  });

  it("does not render an empty-state lie when the inbox request fails", () => {
    useDecisionInbox.mockReturnValue({
      data: undefined,
      error: new Error("offline"),
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    });
    render(<ApprovalInbox variant="sidebar" />);
    fireEvent.click(screen.getByRole("button", { name: /0 pending decisions/i }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not load pending decisions.",
    );
    expect(
      screen.queryByText("Nothing is waiting for a decision."),
    ).not.toBeInTheDocument();
  });
});
