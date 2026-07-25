import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StatusEntity } from "@/lib/types";

const replace = vi.fn();
const mutation = { mutateAsync: vi.fn(), isPending: false };
let statusError: Error | null = null;
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
    data: {
      agents: [],
      tasks: [],
      approvals: [],
      playbook_runs: runs,
      worker_pool: {
        health: "healthy",
        capacity: 2,
        online: 2,
        busy: 1,
        idle: 1,
        pending_depth: 3,
        oldest_pending_at: "2026-07-25T00:00:00Z",
        oldest_pending_age_seconds: 90,
        slots: [
          {
            id: "slot-1",
            slot: 0,
            state: "busy",
            heartbeat_at: "2026-07-25T00:01:00Z",
            heartbeat_age_seconds: 2,
            current_run: {
              id: "run-awaiting",
              playbook_name: "Email exposure triage",
              engagement_slug: "acme",
              steps_total: 10,
              steps_completed: 4,
            },
            last_error: null,
          },
          {
            id: "slot-2",
            slot: 1,
            state: "idle",
            heartbeat_at: "2026-07-25T00:01:00Z",
            heartbeat_age_seconds: 3,
            current_run: null,
            last_error: null,
          },
        ],
        recent_failures: [],
      },
    },
    error: statusError,
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
  beforeEach(() => {
    vi.clearAllMocks();
    statusError = null;
  });

  it("shows truthful worker loaders, queue age, and progress", async () => {
    const user = userEvent.setup();
    render(<StatusView slug="acme" canWrite />);

    expect(screen.getByRole("heading", { name: "Playbook workers" })).toBeInTheDocument();
    expect(screen.getByText(/1 working · 1 ready · 3 queued here/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: /email exposure triage progress/i })).toHaveAttribute(
      "aria-valuenow",
      "4",
    );
    expect(screen.getByText("4/10 steps")).toBeInTheDocument();
    expect(screen.getByText("Oldest wait 1m")).not.toHaveAttribute("aria-live");

    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(screen.getByText("Managing run-awaiting")).toBeInTheDocument();
  });

  it("stops presenting cached worker telemetry as live after a refresh failure", () => {
    const { rerender } = render(<StatusView slug="acme" canWrite />);
    statusError = new Error("network unavailable");
    rerender(<StatusView slug="acme" canWrite />);

    expect(
      screen.getByText(/worker telemetry refresh failed/i),
    ).toHaveAttribute("role", "alert");
    expect(screen.getByText("unknown")).toBeInTheDocument();
    expect(screen.getAllByText("stale")).toHaveLength(2);
  });

  it("keeps the run list visible while switching the side-by-side manage pane", async () => {
    const user = userEvent.setup();
    render(<StatusView slug="acme" canWrite />);

    await user.click(screen.getByRole("button", { name: /review decision email exposure/i }));
    expect(screen.getByText("Managing run-awaiting")).toBeInTheDocument();
    expect(screen.getByText("Passive reconnaissance")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /manage passive reconnaissance/i }));
    expect(screen.queryByText("Managing run-awaiting")).not.toBeInTheDocument();
    expect(screen.getByText("Managing run-complete")).toBeInTheDocument();
    expect(screen.getAllByText("Email exposure triage").length).toBeGreaterThan(0);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
