import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PlaybookRead, ScopeItem } from "@/lib/types";

// Component-layer proof: the KickRunModal is the v3 playbook kickoff surface.
// It now presents the engagement's EXISTING scope as a picker (no re-typing),
// so these tests lock in the picker behaviour + the submit payload, and prove
// @testing-library/react works against the Radix Dialog primitives.

const mockMutateAsync = vi.fn().mockResolvedValue(undefined);
const mockCreate = vi.fn((_slug: string) => ({
  mutateAsync: mockMutateAsync,
  isPending: false,
}));

const scopeItems: ScopeItem[] = [
  {
    id: "s1",
    engagement_id: "e1",
    kind: "domain",
    value: "foo.example",
    is_exclusion: false,
    note: null,
    created_at: "",
    updated_at: "",
  },
  {
    id: "s2",
    engagement_id: "e1",
    kind: "domain",
    value: "bar.example",
    is_exclusion: false,
    note: null,
    created_at: "",
    updated_at: "",
  },
  {
    id: "s3",
    engagement_id: "e1",
    kind: "ip",
    value: "10.0.0.9",
    is_exclusion: false,
    note: "edge host",
    created_at: "",
    updated_at: "",
  },
  {
    id: "s4",
    engagement_id: "e1",
    kind: "domain",
    value: "excluded.example",
    is_exclusion: true, // must NOT be offered
    note: null,
    created_at: "",
    updated_at: "",
  },
  {
    id: "s5",
    engagement_id: "e1",
    kind: "email",
    value: "analyst@example.com",
    is_exclusion: false,
    note: null,
    created_at: "",
    updated_at: "",
  },
];

let mockScopeData: ScopeItem[] | undefined = scopeItems;
let mockScopeError: Error | null = null;
let mockProviderKeys: Array<{ id: string; provider: string }> | undefined = [
  { id: "key-free", provider: "freeipapi" },
  { id: "key-ipinfo", provider: "ipinfo" },
];
let mockProviderKeysError: Error | null = null;
const mockProviderKeysRefetch = vi.fn();
const mockScopeRefetch = vi.fn();
const mockPlanRefetch = vi.fn();
const mockUsePlan = vi.fn(
  (_slug: string, request: { scope_subset: string[] } | null) => ({
    data: request
      ? {
          format_version: 1,
          plan_sha256: "a".repeat(64),
          playbook_id: "pb-1",
          playbook_slug: "osint-enrichment",
          playbook_version: 3,
          playbook_name: "OSINT Enrichment",
          approval_required: false,
          required_executor: "mcp",
          execution_paths: ["Built-in", "Connected service"],
          required_credentials: ["freeipapi", "ipinfo"],
          scope_subset: request.scope_subset,
          minimum_calls: request.scope_subset.length * 5,
          dynamic_expansion: true,
          steps: Array.from({ length: 5 }, (_, index) => ({
            step_id: `step-${index}`,
            sort_order: index * 10,
            tool_slug: `tool-${index}`,
            description:
              index === 0 ? "Resolve PTR hostname." : `Step ${index + 1}`,
            transport: "mcp",
            risk: "passive",
            credential: null,
            arguments_sha256: "c".repeat(64),
            coverage_node_ids: [],
            target_count: request.scope_subset.length,
            expands_targets: index === 4,
            target_source: index === 4 ? "discovered_domains" : null,
            on_error: "continue",
          })),
          safety_notes: ["Every expanded target is checked."],
        }
      : undefined,
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: mockPlanRefetch,
  }),
);
const mockUseScope = vi.fn((_slug: string) => ({
  data: mockScopeData,
  isLoading: false,
  isFetching: false,
  error: mockScopeError,
  refetch: mockScopeRefetch,
}));

vi.mock("@/lib/hooks", () => ({
  useCreatePlaybookRunMutation: (slug: string) => mockCreate(slug),
  usePlaybookRunPlan: (slug: string, request: { scope_subset: string[] } | null) =>
    mockUsePlan(slug, request),
  useProviderKeys: () => ({
    data: mockProviderKeys,
    isLoading: mockProviderKeys === undefined && mockProviderKeysError === null,
    isFetching: false,
    error: mockProviderKeysError,
    refetch: mockProviderKeysRefetch,
  }),
  useScope: (slug: string) => mockUseScope(slug),
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
  required_executor: "mcp",
  required_credentials: ["freeipapi", "ipinfo"],
  step_preview: ["Resolve PTR hostname.", "Collect ASN context."],
  expands_targets: true,
  execution_paths: ["Built-in", "Connected service"],
};

beforeEach(() => {
  mockMutateAsync.mockClear();
  mockCreate.mockClear();
  mockUseScope.mockClear();
  mockScopeRefetch.mockClear();
  mockPlanRefetch.mockClear();
  mockUsePlan.mockClear();
  mockScopeData = scopeItems;
  mockScopeError = null;
  mockProviderKeysError = null;
  mockProviderKeys = [
    { id: "key-free", provider: "freeipapi" },
    { id: "key-ipinfo", provider: "ipinfo" },
  ];
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("KickRunModal", () => {
  it("offers only included targets compatible with the playbook", () => {
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("foo.example")).toBeInTheDocument();
    expect(screen.getByText("bar.example")).toBeInTheDocument();
    expect(screen.queryByText("10.0.0.9")).not.toBeInTheDocument();
    expect(screen.queryByText("excluded.example")).not.toBeInTheDocument();
  });

  it("removes includes overridden by current exclusions", () => {
    mockScopeData = scopeItems.map((item) =>
      item.id === "s1"
        ? { ...item, is_effectively_in_scope: false }
        : { ...item, is_effectively_in_scope: !item.is_exclusion },
    );
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByText("foo.example")).not.toBeInTheDocument();
    expect(screen.getByText("bar.example")).toBeInTheDocument();
  });

  it("offers scoped mailboxes to email playbooks only", () => {
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={{
          ...playbook,
          slug: "email-exposure-triage",
          name: "Email exposure triage",
          applies_to_asset_class: "email",
          required_executor: "internal",
        }}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("analyst@example.com")).toBeInTheDocument();
    expect(screen.queryByText("foo.example")).not.toBeInTheDocument();
    expect(screen.queryByText("10.0.0.9")).not.toBeInTheDocument();
  });

  it("submits only the selected targets in the run payload", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={onClose}
      />,
    );

    await user.click(screen.getByText("foo.example"));
    await user.click(screen.getByText("bar.example"));
    await user.click(screen.getByRole("button", { name: /kick run/i }));

    await waitFor(() => expect(mockMutateAsync).toHaveBeenCalledTimes(1));
    expect(mockMutateAsync).toHaveBeenCalledWith({
      playbook_slug: "osint-enrichment",
      playbook_version: 3,
      scope_subset: ["foo.example", "bar.example"],
      executor: "mcp",
      plan_sha256: "a".repeat(64),
    });
    expect(onClose).toHaveBeenCalled();
  });

  it("clears a selected target when scope changes while open", async () => {
    const user = userEvent.setup();
    const props = {
      engagementSlug: "acme",
      playbook,
      onClose: vi.fn(),
    };
    const { rerender } = render(<KickRunModal {...props} />);
    await user.click(screen.getByText("foo.example"));
    expect(screen.getByText(/1 selected/i)).toBeInTheDocument();

    mockScopeData = scopeItems.map((item) =>
      item.id === "s1"
        ? { ...item, is_effectively_in_scope: false }
        : item,
    );
    rerender(<KickRunModal {...props} />);

    await waitFor(() => expect(screen.getByText(/0 selected/i)).toBeInTheDocument());
    expect(screen.queryByText("foo.example")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /kick run/i })).toBeDisabled();
  });

  it("shows the catalog-selected execution path without an incompatible choice", () => {
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("Selected automatically")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Built-in + Connected service — each step is routed to its server-approved transport.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^internal/i })).not.toBeInTheDocument();
  });

  it("offers every effective exact kind for a scope hygiene review", async () => {
    const user = userEvent.setup();
    const hygiene = {
      ...playbook,
      id: "scope-hygiene",
      slug: "scope-hygiene-review",
      name: "Scope hygiene review",
      applies_to_asset_class: "scope",
      step_count: 1,
      required_executor: "internal" as const,
      required_credentials: [],
      execution_paths: ["Built-in"],
    };
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={hygiene}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("foo.example")).toBeInTheDocument();
    expect(screen.getByText("10.0.0.9")).toBeInTheDocument();
    expect(screen.getByText("analyst@example.com")).toBeInTheDocument();
    expect(screen.queryByText("excluded.example")).not.toBeInTheDocument();
    expect(screen.getAllByText("client-defined").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Select all" }));
    await user.click(screen.getByRole("button", { name: "Kick run" }));
    expect(mockMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        scope_subset: [
          "foo.example",
          "bar.example",
          "10.0.0.9",
          "analyst@example.com",
        ],
      }),
    );
  });

  it("preselects an exact effective entity target without trusting arbitrary values", async () => {
    const user = userEvent.setup();
    const onStarted = vi.fn();
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        initialTarget={{ type: "domain", value: "foo.example" }}
        onStarted={onStarted}
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText(/1 selected/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Kick run" }));
    expect(mockMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ scope_subset: ["foo.example"] }),
    );
    expect(onStarted).toHaveBeenCalledOnce();
  });

  it("does not preselect an absent or incompatible entity target", async () => {
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        initialTarget={{ type: "ip", value: "10.0.0.9" }}
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText(/0 selected/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kick run" })).toBeDisabled();
  });

  it("previews steps, minimum calls, and safe discovery expansion", async () => {
    const user = userEvent.setup();
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("Resolve PTR hostname.")).toBeInTheDocument();
    expect(screen.getByText(/select targets to generate/i)).toBeInTheDocument();
    await user.click(screen.getByText("foo.example"));
    expect(screen.getByText(/5 initial calls; hard limit 500/i)).toBeInTheDocument();
    expect(screen.getByText(/authorized discoveries may add calls/i)).toBeInTheDocument();
    expect(screen.getByText(/no additional approval gate/i)).toBeInTheDocument();
    expect(screen.getByText("Every expanded target is checked.")).toBeInTheDocument();
    expect(screen.getByText(/plan aaaaaaaaaaaa/i)).toBeInTheDocument();
  });

  it("previews required requester-owned credentials", () => {
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "freeipapi: Configured" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "ipinfo: Configured" }),
    ).toBeDisabled();
    expect(screen.getByText(/never persisted in the playbook or run/i)).toBeInTheDocument();
  });

  it("shows credential lookup failures instead of claiming keys are missing", async () => {
    mockProviderKeys = undefined;
    mockProviderKeysError = new Error("key service unavailable");
    const user = userEvent.setup();
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={{ ...playbook, required_credentials: ["ipinfo"] }}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText(/could not check requester credentials/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ipinfo: Unavailable" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /foo\.example/i }));
    expect(screen.getByRole("button", { name: "Kick run" })).toBeDisabled();
  });

  it("lets the requester add a missing dependency without leaving kickoff", async () => {
    mockProviderKeys = [];
    const user = userEvent.setup();
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={{ ...playbook, required_credentials: ["ipinfo"] }}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("checkbox", { name: /foo\.example/i }));
    expect(screen.getByRole("button", { name: "Kick run" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "ipinfo: Add key" }));
    expect(screen.getByLabelText("Provider")).toHaveValue("ipinfo");
    expect(screen.getByLabelText("API key")).toBeInTheDocument();
  });

  it("select-all picks every non-exclusion target", async () => {
    const user = userEvent.setup();
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: /select all/i }));
    await user.click(screen.getByRole("button", { name: /kick run/i }));
    await waitFor(() => expect(mockMutateAsync).toHaveBeenCalledTimes(1));
    expect(mockMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        scope_subset: ["foo.example", "bar.example"],
      }),
    );
  });

  it("disables Kick until at least one target is selected", async () => {
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
    await user.click(screen.getByText("foo.example"));
    expect(kick).toBeEnabled();
  });

  it("guides the analyst to add scope when the engagement has none", () => {
    mockScopeData = [];
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/no included domain targets are available/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /kick run/i })).toBeDisabled();
    expect(screen.getByRole("link", { name: /scope tab/i })).toHaveAttribute(
      "href",
      expect.stringContaining("view=scope"),
    );
  });

  it("keeps cached compatible targets visible after a refresh error", () => {
    mockScopeError = new Error("refresh failed");
    render(
      <KickRunModal
        engagementSlug="acme"
        playbook={playbook}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Refresh failed; showing cached data.",
    );
    expect(screen.getByText("foo.example")).toBeInTheDocument();
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
    await user.click(screen.getByText("foo.example"));
    await user.click(screen.getByRole("button", { name: /kick run/i }));
    await waitFor(() =>
      expect(screen.getByText(/engagement not active/i)).toBeInTheDocument(),
    );
    expect(onClose).not.toHaveBeenCalled();
  });
});
