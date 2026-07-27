import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const remove = { mutateAsync: vi.fn(), isPending: false };
const invalidateQueries = vi.fn();
let scopeItems: Array<Record<string, unknown>> = [];

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries }),
}));
vi.mock("@/components/scope-importer", () => ({
  ScopeImporter: () => null,
}));
vi.mock("@/lib/hooks", () => ({
  qk: {
    scope: (slug: string) => ["scope", slug],
    entities: (slug: string) => ["entities", slug],
    engagements: () => ["engagements"],
  },
  useScope: () => ({
    data: scopeItems,
    error: null,
    isLoading: false,
  }),
  useCreateScopeItemMutation: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteScopeItemMutation: () => remove,
}));

import { ScopeEditor } from "@/components/scope-editor";

describe("ScopeEditor deletion", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    remove.mutateAsync.mockResolvedValue(undefined);
    scopeItems = [
      {
        id: "scope-1",
        engagement_id: "eng-1",
        kind: "domain",
        value: "example.com",
        is_exclusion: false,
        note: null,
        source: "defined",
        is_effectively_in_scope: true,
        effective_scope: {
          state: "included",
          allowed: true,
          reason_code: "matched_include",
          reason: "Included by domain rule example.com.",
          target_kind: "domain",
          matched_include_id: "scope-1",
          matched_exclusion_id: null,
        },
        created_at: "",
        updated_at: "",
      },
    ];
  });

  it("hides exclusions until the analyst explicitly reviews them", async () => {
    const user = userEvent.setup();
    scopeItems.push({
      id: "scope-exclusion",
      engagement_id: "eng-1",
      kind: "domain",
      value: "vendor.example",
      is_exclusion: true,
      note: "provider boundary",
      source: "defined",
      is_effectively_in_scope: false,
      effective_scope: {
        state: "excluded",
        allowed: false,
        reason_code: "matched_exclusion",
        reason: "Excluded by domain rule vendor.example.",
        target_kind: "domain",
        matched_include_id: null,
        matched_exclusion_id: "scope-exclusion",
      },
      created_at: "",
      updated_at: "",
    });
    scopeItems.push({
      id: "scope-unmatched",
      engagement_id: "eng-1",
      kind: "domain",
      value: "candidate.example",
      is_exclusion: false,
      note: "discovered candidate",
      source: "found",
      is_effectively_in_scope: true,
      effective_scope: {
        state: "unmatched",
        allowed: false,
        reason_code: "no_matching_include",
        reason: "No include authorizes candidate.example.",
        target_kind: "domain",
        matched_include_id: null,
        matched_exclusion_id: null,
      },
      created_at: "",
      updated_at: "",
    });

    render(<ScopeEditor slug="acme" canWrite />);

    expect(screen.getByText("example.com")).toBeInTheDocument();
    expect(screen.queryByText("vendor.example")).not.toBeInTheDocument();
    expect(screen.queryByText("candidate.example")).not.toBeInTheDocument();
    expect(screen.getByText("1 actionable item")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Review exclusions/outliers (2)" }),
    );
    expect(screen.getByText("vendor.example")).toBeInTheDocument();
    expect(screen.getByText("candidate.example")).toBeInTheDocument();
    expect(screen.getByText("not actionable")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Hide exclusions/outliers" }),
    );
    expect(screen.queryByText("vendor.example")).not.toBeInTheDocument();
  });

  it("uses an in-app confirmation window before removing scope", async () => {
    const user = userEvent.setup();
    render(<ScopeEditor slug="acme" canWrite />);

    await user.click(screen.getByRole("button", { name: /delete scope item example.com/i }));
    expect(screen.getByRole("dialog", { name: /delete scope item/i })).toBeInTheDocument();
    expect(screen.getByText(/eligible targets for new playbook runs/i)).toBeInTheDocument();
    expect(remove.mutateAsync).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /keep item/i }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /delete scope item example.com/i }));
    await user.click(screen.getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(remove.mutateAsync).toHaveBeenCalledWith("scope-1"));
  });
});
