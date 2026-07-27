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
      requested_by: null,
      approved_by: null,
      approved_at: null,
      approval_reason: null,
      rejected_by: null,
      rejected_at: null,
      rejection_reason: null,
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
