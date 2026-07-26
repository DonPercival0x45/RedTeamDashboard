import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EntityReviewDialog } from "@/components/entity-review-dialog";

const previewEntityReview = vi.fn();
const applyEntityReview = vi.fn();
vi.mock("@/lib/api", () => ({
  previewEntityReview: (...args: unknown[]) => previewEntityReview(...args),
  applyEntityReview: (...args: unknown[]) => applyEntityReview(...args),
}));

describe("EntityReviewDialog", () => {
  it("previews and applies a reversible bounded cascade", async () => {
    const user = userEvent.setup();
    previewEntityReview.mockResolvedValue({
      action: "exclude",
      cascade: true,
      preview_sha256: "a".repeat(64),
      entities: [
        {
          type: "domain",
          value: "target.example",
          depth: 0,
          reason: "Selected by analyst",
          scope_kind: "domain",
          exact_include_ids: ["include-1"],
          exact_exclusion_ids: [],
          managed_exclusion_ids: [],
        },
        {
          type: "ip",
          value: "203.0.113.10",
          depth: 1,
          reason: "Discovered by DNS while assessing target.example",
          scope_kind: "ip",
          exact_include_ids: [],
          exact_exclusion_ids: [],
          managed_exclusion_ids: [],
        },
      ],
      findings: [{
        id: "finding-1",
        title: "DNS inventory",
        target: "target.example",
        source_tool: "dns_inventory",
        current_exclusion: null,
        parent_type: "domain",
        parent_value: "target.example",
        depth: 0,
      }],
      finding_ids: ["finding-1"],
      exact_include_conflicts: 1,
      exclusions_to_create: 2,
      managed_exclusions_to_remove: 0,
      findings_to_mark_out_of_scope: 1,
      findings_to_restore: 0,
      truncated: false,
    });
    applyEntityReview.mockResolvedValue({ reviewed: 2 });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <EntityReviewDialog
          slug="example"
          targets={[{ type: "domain", value: "target.example" }]}
          action="exclude"
          open
          onOpenChange={vi.fn()}
          onApplied={vi.fn()}
        />
      </QueryClientProvider>,
    );

    await user.click(screen.getByLabelText(/Cascade to related discoveries/));
    await user.type(screen.getByLabelText("Entity review reason"), "Vendor branch");
    await user.click(screen.getByRole("button", { name: "Preview impact" }));
    expect(await screen.findByText("203.0.113.10")).toBeInTheDocument();
    expect(screen.getByText("Findings marked out of scope")).toBeInTheDocument();
    expect(screen.getByText(/exact include rule remains recorded but dormant/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Refresh preview" }));
    await waitFor(() => expect(previewEntityReview).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("button", { name: "Apply exclusions" }));
    await waitFor(() =>
      expect(applyEntityReview).toHaveBeenCalledWith(
        "example",
        expect.objectContaining({
          cascade: true,
          reason: "Vendor branch",
          remove_conflicting_exact_includes: false,
          preview_sha256: "a".repeat(64),
        }),
      ),
    );
  });
});
