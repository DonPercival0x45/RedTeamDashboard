import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const remove = { mutateAsync: vi.fn(), isPending: false };
const invalidateQueries = vi.fn();

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
    data: [
      {
        id: "scope-1",
        engagement_id: "eng-1",
        kind: "domain",
        value: "example.com",
        is_exclusion: false,
        note: null,
        source: "defined",
        is_effectively_in_scope: true,
        created_at: "",
        updated_at: "",
      },
    ],
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
