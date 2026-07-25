import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StatusEntity } from "@/lib/types";

const replace = vi.fn();
const mutation = { mutateAsync: vi.fn(), isPending: false };
const runs: StatusEntity[] = [
  {
    id: "run-awaiting",
    kind: "playbook",
    title: "Email exposure triage",
    subtitle: "analyst@example.com",
    color: "pending",
    raw_status: "awaiting_approval",
    started_at: "2026-07-25T00:00:00Z",
    completed_at: null,
    retryable: false,
    log: {},
    history: [],
    run_slug: "rt-awaiting",
    outcome: null,
    synopsis: "Waiting for an analyst decision",
  },
  {
    id: "run-complete",
    kind: "playbook",
    title: "Passive reconnaissance",
    subtitle: "example.com",
    color: "completed",
    raw_status: "completed",
    started_at: "2026-07-24T00:00:00Z",
    completed_at: "2026-07-24T00:01:00Z",
    retryable: false,
    log: {},
    history: [],
    run_slug: "rt-complete",
    outcome: "success",
    synopsis: "Completed successfully",
  },
];

vi.mock("next/navigation", () => ({
  usePathname: () => "/e",
  useRouter: () => ({ replace }),
  useSearchParams: () => ({ get: () => null, toString: () => "" }),
}));

vi.mock("@/components/attribution-table", () => ({
  AttributionTable: () => <div>Attribution</div>,
}));
vi.mock("@/components/date-time", () => ({
  DateTime: ({ value }: { value: string }) => <span>{value}</span>,
}));
vi.mock("@/components/playbooks/run-detail-modal", () => ({
  RunDetailPanel: ({ runId, onClose }: { runId: string; onClose: () => void }) => (
    <section aria-label="run management">
      <p>Managing {runId}</p>
      <button onClick={onClose}>Close management</button>
    </section>
  ),
}));
vi.mock("@/lib/hooks", () => ({
  useEngagementStatus: () => ({
    data: { agents: [], tasks: [], approvals: [], playbook_runs: runs },
    error: null,
    refetch: vi.fn(),
    isFetching: false,
  }),
  useRetryTaskMutation: () => mutation,
  useRetryAgentExecutionMutation: () => mutation,
  useCancelTaskMutation: () => mutation,
  useCancelAgentExecutionMutation: () => mutation,
  useStatusSteps: () => ({ data: [], isLoading: false, error: null }),
}));

import { StatusView } from "@/components/status-view";

describe("StatusView playbook management", () => {
  beforeEach(() => vi.clearAllMocks());

  it("keeps the run list visible while switching the side-by-side manage pane", async () => {
    const user = userEvent.setup();
    render(<StatusView slug="acme" canWrite />);

    await user.click(screen.getByRole("button", { name: /review decision email exposure/i }));
    expect(screen.getByText("Managing run-awaiting")).toBeInTheDocument();
    expect(screen.getByText("Passive reconnaissance")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /manage passive reconnaissance/i }));
    expect(screen.queryByText("Managing run-awaiting")).not.toBeInTheDocument();
    expect(screen.getByText("Managing run-complete")).toBeInTheDocument();
    expect(screen.getByText("Email exposure triage")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
