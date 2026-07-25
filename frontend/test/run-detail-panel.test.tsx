import { useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const approve = { mutateAsync: vi.fn(), isPending: false };
const reject = { mutateAsync: vi.fn(), isPending: false };
const cancel = { mutateAsync: vi.fn(), isPending: false };

vi.mock("@/lib/hooks", () => ({
  usePlaybookRun: () => ({
    data: {
      id: "run-1",
      engagement_id: "eng-1",
      engagement_slug: "receipt-demo",
      playbook_id: "pb-1",
      playbook_slug: "email-exposure-triage",
      playbook_version: 1,
      status: "awaiting_approval",
      executor: "internal",
      scope_subset: ["analyst@example.com"],
      started_at: null,
      completed_at: null,
      steps_total: 1,
      steps_succeeded: 0,
      steps_failed: 0,
      findings_new: 0,
      findings_unvalidated: 0,
      findings_high_severity: 0,
      findings_total: 0,
      last_error: null,
      plan_sha256: "b".repeat(64),
      planned_at: "2026-07-25T11:59:00Z",
      execution_plan: {
        format_version: 1,
        plan_sha256: "b".repeat(64),
        playbook_id: "pb-1",
        playbook_slug: "email-exposure-triage",
        playbook_version: 1,
        playbook_name: "Email exposure triage",
        approval_required: true,
        required_executor: "internal",
        execution_paths: ["Built-in"],
        required_credentials: [],
        scope_subset: ["analyst@example.com"],
        minimum_calls: 1,
        dynamic_expansion: false,
        steps: [
          {
            step_id: "catalog-step-1",
            sort_order: 10,
            tool_slug: "breach-lookup",
            description: "Review imported exposure evidence.",
            transport: "internal",
            risk: "passive",
            credential: null,
            arguments_sha256: "c".repeat(64),
            coverage_node_ids: [],
            target_count: 1,
            expands_targets: false,
            target_source: null,
            on_error: "continue",
          },
        ],
        safety_notes: ["Every target is scope validated."],
      },
      requested_by: null,
      approved_by: null,
      approved_at: null,
      approval_reason: null,
      rejected_by: null,
      rejected_at: null,
      rejection_reason: null,
      step_executions: [
        {
          id: "step-1",
          playbook_step_id: "catalog-step-1",
          sort_order: 10,
          tool_slug: "breach-lookup",
          target: "analyst@example.com",
          transport: "internal",
          attempt: 1,
          status: "failed",
          arguments: { email: "analyst@example.com" },
          started_at: "2026-07-25T12:00:00Z",
          completed_at: "2026-07-25T12:00:01Z",
          duration_ms: 1250,
          error: "Imported evidence was unavailable",
          evidence: {
            id: "evidence-1",
            finding_id: "finding-1",
            sha256: "a".repeat(64),
            size_bytes: 128,
            truncated: false,
            redacted: true,
          },
        },
      ],
    },
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useEvidenceArtifact: () => ({
    data: {
      id: "evidence-1",
      engagement_id: "eng-1",
      playbook_run_id: "run-1",
      playbook_step_execution_id: "step-1",
      finding_id: "finding-1",
      kind: "tool_output",
      source_tool: "breach-lookup",
      target: "analyst@example.com",
      payload: { ok: false, error: "Imported evidence was unavailable" },
      sha256: "a".repeat(64),
      size_bytes: 128,
      truncated: false,
      redacted: true,
      captured_at: "2026-07-25T12:00:01Z",
    },
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useApprovePlaybookRunMutation: () => approve,
  useRejectPlaybookRunMutation: () => reject,
  useCancelPlaybookRunMutation: () => cancel,
}));

import {
  RunDetailModal,
  RunDetailPanel,
} from "@/components/playbooks/run-detail-modal";

describe("RunDetailPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    approve.mutateAsync.mockResolvedValue(undefined);
    reject.mutateAsync.mockResolvedValue(undefined);
  });

  it("shows the immutable execution boundary beside approval controls", () => {
    render(<RunDetailPanel runId="run-1" onClose={vi.fn()} canWrite />);

    const section = screen.getByRole("region", {
      name: "Execution boundary for review",
    });
    expect(within(section).getByText(/1 minimum calls/)).toBeInTheDocument();
    expect(
      within(section).getByText(/Review imported exposure evidence/),
    ).toBeInTheDocument();
  });

  it("shows durable per-target step receipts without a fake retry control", () => {
    render(<RunDetailPanel runId="run-1" onClose={vi.fn()} canWrite />);

    const section = screen.getByRole("region", { name: "Step receipts" });
    expect(
      within(section).getByText("Execution 1: breach-lookup"),
    ).toBeInTheDocument();
    expect(within(section).getByText("analyst@example.com")).toBeInTheDocument();
    expect(within(section).getByText("Failed")).toBeInTheDocument();
    expect(within(section).getByText("1.3 s")).toBeInTheDocument();
    expect(
      within(section).getByText("Imported evidence was unavailable"),
    ).toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: /retry/i })).toBeNull();
  });

  it("loads redacted evidence on demand and links its canonical finding", async () => {
    const user = userEvent.setup();
    render(<RunDetailPanel runId="run-1" onClose={vi.fn()} canWrite />);

    await user.click(screen.getByRole("button", { name: "View evidence" }));

    expect(screen.getByText(/Redacted JSON/)).toBeInTheDocument();
    expect(
      screen.getAllByText(/Imported evidence was unavailable/),
    ).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Open canonical finding" })).toHaveAttribute(
      "href",
      "/e/findings/finding-1?slug=receipt-demo",
    );
  });

  it("approves in place without closing the Runs manage pane", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<RunDetailPanel runId="run-1" onClose={onClose} canWrite />);

    await user.type(screen.getByPlaceholderText(/approval reason/i), "Target confirmed");
    await user.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(approve.mutateAsync).toHaveBeenCalledWith({
        runId: "run-1",
        reason: "Target confirmed",
      }),
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText("Manage run")).toBeInTheDocument();
  });

  it("keeps the legacy modal accessible and restores focus when closed", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>Open run</button>
          {open ? (
            <RunDetailModal runId="run-1" onClose={() => setOpen(false)} />
          ) : null}
        </>
      );
    }
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open run" });
    await user.click(opener);

    const dialog = screen.getByRole("dialog", { name: "Manage playbook run" });
    expect(dialog).toHaveAccessibleDescription(
      "Review lifecycle, findings, targets, and any required decision.",
    );
    await user.click(within(dialog).getAllByRole("button", { name: "Close" })[0]);
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("requires a rejection reason and rejects without closing the pane", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<RunDetailPanel runId="run-1" onClose={onClose} canWrite />);

    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: /confirm reject/i }));
    expect(screen.getByText(/reason is required/i)).toBeInTheDocument();
    expect(reject.mutateAsync).not.toHaveBeenCalled();

    await user.type(screen.getByPlaceholderText(/reason for rejecting/i), "Not authorized");
    await user.click(screen.getByRole("button", { name: /confirm reject/i }));
    await waitFor(() =>
      expect(reject.mutateAsync).toHaveBeenCalledWith({
        runId: "run-1",
        reason: "Not authorized",
      }),
    );
    expect(onClose).not.toHaveBeenCalled();
  });
});
