import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PlaybookRead } from "@/lib/types";

// Component-layer proof: the KickRunModal is the v3 playbook kickoff surface
// and its scope parsing is the exact UX flagged by the audit (free-form text
// passed straight through). This test locks in the current submit payload so
// any future change to scope parsing is deliberate, not accidental, and it
// proves @testing-library/react works against the Radix Dialog primitives.

const mockMutateAsync = vi.fn().mockResolvedValue(undefined);
const mockCreate = vi.fn(() => ({
  mutateAsync: mockMutateAsync,
  isPending: false,
}));

vi.mock("@/lib/hooks", () => ({
  useCreatePlaybookRunMutation: (slug: string) => mockCreate(slug),
}));

import { KickRunModal } from "@/components/playbooks/kick-run-modal";

const playbook: PlaybookRead = {
  id: "pb-1",
  slug: "osint-enrichment",
  version: 3,
  name: "OSINT Enrichment",
  description: "dns + whois",
  applies_to_asset_class: "domain",
  active: false,
  step_count: 5,
};

beforeEach(() => {
  mockMutateAsync.mockClear();
  mockCreate.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("KickRunModal", () => {
  it("parses comma + newline separated scope into the run payload", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={onClose}
      />,
    );

    const scope = screen.getByLabelText(/scope selection/i);
    await user.type(scope, "foo.example, bar.example{Enter}baz.example");

    await user.click(screen.getByRole("button", { name: /kick run/i }));

    await waitFor(() => expect(mockMutateAsync).toHaveBeenCalledTimes(1));
    expect(mockMutateAsync).toHaveBeenCalledWith({
      playbook_slug: "osint-enrichment",
      playbook_version: 3,
      scope_subset: ["foo.example", "bar.example", "baz.example"],
      executor: "internal",
    });
    // onClose fires after a successful kick so the parent unmounts the modal.
    expect(onClose).toHaveBeenCalled();
  });

  it("disables Kick until at least one scope item is entered", async () => {
    const user = userEvent.setup();
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={vi.fn()}
      />,
    );
    const kick = screen.getByRole("button", { name: /kick run/i });
    expect(kick).toBeDisabled();

    await user.type(screen.getByLabelText(/scope selection/i), "only.example");
    expect(kick).toBeEnabled();
  });

  it("shows the error message when the kick fails and keeps the modal open", async () => {
    mockMutateAsync.mockRejectedValueOnce(new Error("engagement not active"));
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={onClose}
      />,
    );

    await user.type(screen.getByLabelText(/scope selection/i), "x.example");
    await user.click(screen.getByRole("button", { name: /kick run/i }));

    await waitFor(() =>
      expect(screen.getByText(/engagement not active/i)).toBeInTheDocument(),
    );
    expect(onClose).not.toHaveBeenCalled();
  });
});
