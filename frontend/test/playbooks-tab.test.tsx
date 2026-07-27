import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PlaybookRead } from "@/lib/types";

const playbooks: PlaybookRead[] = [
  {
    id: "ip",
    slug: "ip-enrichment",
    version: 1,
    name: "IP enrichment",
    description: "IP context",
    applies_to_asset_class: "ip",
    applicable_entity_types: ["ip"],
    category: "discovery",
    origin: "system",
    can_edit: true,
    has_runs: false,
    active: false,
    step_count: 2,
    required_executor: "mcp",
  },
  {
    id: "posture",
    slug: "domain-posture",
    version: 2,
    name: "Domain posture",
    description: "Headers and mail",
    applies_to_asset_class: "domain",
    applicable_entity_types: ["domain", "subdomain", "host"],
    category: "posture",
    origin: "custom",
    can_edit: true,
    has_runs: true,
    active: false,
    step_count: 3,
    required_executor: "internal",
  },
  {
    id: "discovery",
    slug: "asset-discovery",
    version: 1,
    name: "Asset discovery",
    description: "Passive discovery",
    applies_to_asset_class: "domain",
    applicable_entity_types: ["domain", "subdomain", "host"],
    category: "discovery",
    origin: "system",
    can_edit: true,
    has_runs: false,
    active: false,
    step_count: 1,
    required_executor: "internal",
  },
];

const refetch = vi.fn();
let role = "user";
let runs: any[] = [];
vi.mock("@/lib/hooks", () => ({
  usePlaybooks: () => ({
    data: playbooks,
    isLoading: false,
    isFetching: false,
    error: null,
    refetch,
  }),
  usePlaybookRuns: () => ({
    data: runs,
    isLoading: false,
    isFetching: false,
    error: null,
    refetch,
  }),
  useMe: () => ({ data: { role } }),
}));
vi.mock("@/components/playbooks/kick-run-modal", () => ({
  KickRunModal: ({ playbook }: { playbook: PlaybookRead }) => (
    <div>Kick {playbook.slug}</div>
  ),
}));
vi.mock("@/components/playbooks/run-detail-modal", () => ({
  RunDetailModal: ({ canWrite }: { canWrite?: boolean }) => (
    <div>Run detail writable: {String(canWrite)}</div>
  ),
}));
vi.mock("@/components/playbooks/playbook-editor-modal", () => ({
  PlaybookEditorModal: ({ playbook }: { playbook: PlaybookRead | null }) => (
    <div>{playbook ? `Editor ${playbook.slug}` : "Editor new"}</div>
  ),
}));

import { PlaybooksTab } from "@/components/playbooks/playbooks-tab";

function cardNames(): string[] {
  return screen
    .getAllByRole("heading", { level: 4 })
    .map((heading) => heading.textContent ?? "");
}

describe("PlaybooksTab catalog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    role = "user";
    runs = [];
  });

  it("defaults an entity launch to applicable recipes and category tabs", () => {
    render(
      <PlaybooksTab
        engagementSlug="acme"
        initialTarget={{ type: "subdomain", value: "app.example.com" }}
      />,
    );

    expect(screen.getByText(/Target context · subdomain/i)).toBeInTheDocument();
    expect(cardNames()).toEqual(["Asset discovery", "Domain posture"]);
    expect(screen.queryByRole("heading", { name: "IP enrichment" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Security posture \(1\)/i }));
    expect(cardNames()).toEqual(["Domain posture"]);

    fireEvent.click(screen.getByRole("button", { name: "Show all playbooks" }));
    expect(screen.getByText(/Target context · subdomain/i)).toBeInTheDocument();
    expect(screen.queryByText(/Applicable to subdomain/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Discovery \(2\)/i }));
    expect(cardNames()).toEqual(["Asset discovery", "IP enrichment"]);
  });

  it("sorts deterministically and opens create and immutable-version editing", () => {
    render(<PlaybooksTab engagementSlug="acme" />);

    expect(cardNames()).toEqual([
      "Asset discovery",
      "Domain posture",
      "IP enrichment",
    ]);
    expect(screen.getByText("Custom")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "New playbook" }));
    expect(screen.getByText("Editor new")).toBeInTheDocument();
  });

  it("keeps guest catalog review read-only without mounting kickoff", () => {
    role = "guest";
    render(<PlaybooksTab engagementSlug="acme" />);
    expect(screen.queryByRole("button", { name: "New playbook" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Run Domain posture/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit Domain posture/ })).not.toBeInTheDocument();
    expect(screen.getAllByText("Read-only")).toHaveLength(3);

    runs = [
      {
        id: "run-1",
        playbook_slug: "domain-posture",
        playbook_version: 1,
        status: "awaiting_approval",
        executor: "internal",
        scope_subset: ["example.com"],
        steps_succeeded: 0,
        steps_total: 1,
        started_at: null,
      },
    ];
    const { unmount } = render(<PlaybooksTab engagementSlug="acme" />);
    fireEvent.click(screen.getByRole("button", { name: "Manage domain-posture run" }));
    expect(screen.getByText("Run detail writable: false")).toBeInTheDocument();
    unmount();
  });

  it("uses uniquely named run and edit actions", () => {
    render(<PlaybooksTab engagementSlug="acme" />);
    const postureCard = screen
      .getByRole("heading", { name: "Domain posture" })
      .closest<HTMLElement>("div.rounded-lg");
    expect(postureCard).not.toBeNull();
    expect(within(postureCard!).getByRole("button", { name: "Run Domain posture" })).toBeInTheDocument();
    fireEvent.click(within(postureCard!).getByRole("button", { name: "Edit Domain posture" }));
    expect(screen.getByText("Editor domain-posture")).toBeInTheDocument();
  });
});
