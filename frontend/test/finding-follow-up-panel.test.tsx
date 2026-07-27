import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FindingFollowUpPanel } from "@/components/finding-follow-up-panel";

const getFindingFollowUp = vi.fn();
const createFindingRemediationUpdate = vi.fn();
const createFindingRetest = vi.fn();

vi.mock("@/lib/api", () => ({
  getFindingFollowUp: (...args: unknown[]) => getFindingFollowUp(...args),
  createFindingRemediationUpdate: (...args: unknown[]) =>
    createFindingRemediationUpdate(...args),
  createFindingRetest: (...args: unknown[]) => createFindingRetest(...args),
}));

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FindingFollowUpPanel findingId="finding-1" />
    </QueryClientProvider>,
  );
}

describe("FindingFollowUpPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getFindingFollowUp.mockResolvedValue({
      latest_remediation: null,
      latest_retest: null,
      remediation_updates: [],
      retests: [],
    });
    createFindingRemediationUpdate.mockResolvedValue({ id: "update-1" });
    createFindingRetest.mockResolvedValue({ id: "retest-1" });
  });

  it("tracks client updates and makes retest recording semantics explicit", async () => {
    const user = userEvent.setup();
    renderPanel();

    expect(await screen.findByText("No update recorded")).toBeInTheDocument();
    expect(
      screen.getByText(/does not run a tool or replay an execution/i),
    ).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Client remediation status"), "ready_for_retest");
    await user.type(screen.getByLabelText("Client remediation note"), "Patch deployed");
    await user.click(screen.getByRole("button", { name: "Record update" }));
    await waitFor(() =>
      expect(createFindingRemediationUpdate).toHaveBeenCalledWith("finding-1", {
        status: "ready_for_retest",
        note: "Patch deployed",
      }),
    );

    await user.selectOptions(screen.getByLabelText("Retest outcome"), "not_fixed");
    await user.type(screen.getByLabelText("Retest evidence note"), "Alternate route remains");
    await user.click(screen.getByRole("button", { name: "Record retest" }));
    await waitFor(() =>
      expect(createFindingRetest).toHaveBeenCalledWith("finding-1", {
        outcome: "not_fixed",
        note: "Alternate route remains",
      }),
    );
  });
});
