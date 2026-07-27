import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PlaybookDetail, PlaybookRead } from "@/lib/types";

const create = { mutateAsync: vi.fn(), isPending: false };
const version = { mutateAsync: vi.fn(), isPending: false };
const refetch = vi.fn();
const existing: PlaybookDetail = {
  id: "pb-1",
  slug: "existing-book",
  version: 4,
  name: "Existing book",
  description: "Original recipe",
  applies_to_asset_class: "domain",
  applicable_entity_types: ["domain", "subdomain", "host"],
  category: "discovery",
  origin: "system",
  can_edit: true,
  has_runs: true,
  active: false,
  step_count: 1,
  required_executor: "internal",
  steps: [
    {
      id: "step-1",
      sort_order: 10,
      tool_slug: "whois",
      args_template: { domain: "{{scope_item}}" },
      satisfies_node_ids: [],
      description: "Registration",
    },
  ],
};
let currentDetail = existing;

vi.mock("@/lib/hooks", () => ({
  usePlaybook: (slug: string | null) => ({
    data: slug ? currentDetail : undefined,
    isLoading: false,
    isFetching: false,
    error: null,
    refetch,
  }),
  usePlaybookCatalogOptions: () => ({
    data: {
      categories: ["discovery", "validation", "other"],
      entity_types: ["domain", "subdomain", "host", "ip"],
      tools: [
        {
          slug: "whois",
          name: "WHOIS lookup",
          description: "Registration metadata",
          target_kinds: ["domain"],
          transport: "internal",
          risk: "passive",
          credential: null,
        },
        {
          slug: "ipinfo",
          name: "IP ownership",
          description: "ASN ownership",
          target_kinds: ["ip"],
          transport: "mcp",
          risk: "passive",
          credential: "ipinfo",
        },
      ],
    },
    isLoading: false,
    isFetching: false,
    error: null,
    refetch,
  }),
  useProviderKeys: () => ({ data: [{ provider: "ipinfo" }], refetch }),
  useCreatePlaybookMutation: () => create,
  useCreatePlaybookVersionMutation: () => version,
}));
vi.mock("@/components/quick-add-key", () => ({
  QuickAddKey: () => <div>Quick add key</div>,
}));

import { PlaybookEditorModal } from "@/components/playbooks/playbook-editor-modal";

const existingRead = existing as PlaybookRead;

describe("PlaybookEditorModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentDetail = existing;
    create.mutateAsync.mockResolvedValue({});
    version.mutateAsync.mockResolvedValue({});
  });

  it("creates a complete ordered recipe without code deployment", async () => {
    const user = userEvent.setup();
    render(<PlaybookEditorModal playbook={null} onClose={vi.fn()} />);

    await user.type(screen.getByLabelText("Name"), "Passive DNS review");
    expect(screen.getByLabelText("Slug")).toHaveValue("passive-dns-review");
    await user.click(screen.getByRole("button", { name: "Add step" }));
    await user.click(screen.getByRole("button", { name: "Create playbook" }));

    await waitFor(() => expect(create.mutateAsync).toHaveBeenCalledTimes(1));
    expect(create.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        slug: "passive-dns-review",
        category: "discovery",
        applicable_entity_types: ["domain"],
        steps: [
          expect.objectContaining({
            tool_slug: "whois",
          }),
        ],
      }),
    );
  });

  it("edits an existing book by publishing a new immutable version", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <PlaybookEditorModal playbook={existingRead} onClose={vi.fn()} />,
    );

    expect(await screen.findByDisplayValue("Existing book")).toBeInTheDocument();
    expect(screen.getByText(/creates existing-book v5/i)).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Focused existing book");
    await user.click(screen.getByRole("checkbox", { name: "Host" }));
    expect(screen.getByDisplayValue("Registration")).toBeInTheDocument();
    currentDetail = {
      ...existing,
      id: "pb-2",
      version: 5,
      name: "Concurrent version",
      steps: [{ ...existing.steps[0], id: "step-2" }],
    };
    rerender(<PlaybookEditorModal playbook={existingRead} onClose={vi.fn()} />);
    expect(screen.getByDisplayValue("Focused existing book")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Publish new version" }));

    await waitFor(() => expect(version.mutateAsync).toHaveBeenCalledTimes(1));
    expect(version.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_supersedes_id: "pb-1",
        expected_version: 4,
        name: "Focused existing book",
        applicable_entity_types: ["domain", "subdomain"],
        steps: [
          expect.objectContaining({
            tool_slug: "whois",
            source_step_id: "step-1",
          }),
        ],
      }),
    );
  });

  it("confirms before discarding a changed draft", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<PlaybookEditorModal playbook={null} onClose={onClose} />);
    await user.type(screen.getByLabelText("Name"), "Unpublished work");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("dialog", { name: "Discard playbook draft?" })).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Keep item" }));
    expect(screen.getByDisplayValue("Unpublished work")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("confirms before an incompatible entity family clears steps", async () => {
    const user = userEvent.setup();
    render(<PlaybookEditorModal playbook={null} onClose={vi.fn()} />);
    await user.type(screen.getByLabelText("Name"), "Family change");
    await user.click(screen.getByRole("button", { name: "Add step" }));

    await user.click(screen.getByRole("checkbox", { name: "IP address" }));
    expect(screen.getByRole("dialog", { name: "Change playbook target family?" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("Registration metadata")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep item" }));
    expect(screen.getByDisplayValue("Registration metadata")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "IP address" }));
    await user.click(screen.getByRole("button", { name: "Change and clear steps" }));
    expect(screen.getByText("Add at least one step to publish this playbook.")).toBeInTheDocument();
  });
});
