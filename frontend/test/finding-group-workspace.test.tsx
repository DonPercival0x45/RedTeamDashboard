import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Finding } from "@/lib/types";

const createFindingMock = vi.fn();
const createGroupMock = vi.fn();
const updateGroupMock = vi.fn();
const useFindingGroupsMock = vi.fn();

const findings: Finding[] = [
  {
    id: "finding-alpha",
    thread_id: null,
    tool: "manual",
    target: "alpha.example",
    args: {},
    data: {},
    severity: "low",
    title: "Alpha Finding",
    summary: null,
    phase: "general",
    status: "pending_validation",
    exclusion: null,
    group_key: null,
    item_count: 0,
    validated_at: null,
    observed_at: null,
    burp_serial_number: null,
    created_at: "2026-07-27T00:00:00Z",
    tags: [],
  },
  {
    id: "finding-beta",
    thread_id: null,
    tool: "service_detect",
    target: "192.0.2.10:443",
    args: {},
    data: {},
    severity: "high",
    title: "Beta Finding",
    summary: null,
    phase: "vuln_scan",
    status: "validated",
    exclusion: null,
    group_key: null,
    item_count: 0,
    validated_at: "2026-07-27T00:00:00Z",
    observed_at: null,
    burp_serial_number: null,
    created_at: "2026-07-27T01:00:00Z",
    tags: [],
  },
];

vi.mock("@/lib/hooks", () => ({
  qk: {
    findings: (slug: string) => ["findings", slug],
    findingHierarchy: (slug: string) => ["finding-hierarchy", slug],
    findingGroups: (slug: string) => ["finding-groups", slug],
  },
  useFindingGroups: (...args: unknown[]) => useFindingGroupsMock(...args),
}));

vi.mock("@/lib/api", () => ({
  createFinding: (...args: unknown[]) => createFindingMock(...args),
  createFindingGroup: (...args: unknown[]) => createGroupMock(...args),
  updateFindingGroup: (...args: unknown[]) => updateGroupMock(...args),
  deleteFindingGroup: vi.fn(),
}));

import {
  FindingActionWizard,
  FindingGroupsPanel,
} from "@/components/finding-group-workspace";

function wrapper(children: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>,
  );
}

describe("Finding workspace authoring", () => {
  beforeEach(() => {
    createFindingMock.mockReset();
    createGroupMock.mockReset();
    updateGroupMock.mockReset();
    useFindingGroupsMock.mockReset();
    useFindingGroupsMock.mockReturnValue({ data: [], isLoading: false, error: null });
  });

  it("creates an independent custom Finding from the chooser", async () => {
    createFindingMock.mockResolvedValue({ ...findings[0], title: "Analyst Finding" });
    const onCreated = vi.fn();
    wrapper(
      <FindingActionWizard
        slug="acme"
        findings={[...findings]}
        onClose={vi.fn()}
        onFindingCreated={onCreated}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /custom finding/i }));
    fireEvent.change(screen.getByPlaceholderText(/reflected xss/i), {
      target: { value: "Analyst Finding" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create Finding" }));

    await waitFor(() => expect(createFindingMock).toHaveBeenCalled());
    expect(createFindingMock.mock.calls[0]?.[1]).toMatchObject({
      title: "Analyst Finding",
      severity: "info",
      phase: "general",
    });
    expect(onCreated).toHaveBeenCalled();
  });

  it("walks through a non-destructive group wizard with two Findings", async () => {
    createGroupMock.mockResolvedValue({
      id: "group-1",
      engagement_id: "engagement-1",
      name: "Related exposure",
      rationale: "Same attack path",
      created_by_user_id: null,
      row_version: 1,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      members: [],
      rollup: {
        member_count: 2,
        available_members: 2,
        unavailable_members: 0,
        max_severity: "high",
        status_counts: {},
        excluded_count: 0,
      },
    });
    const onClose = vi.fn();
    wrapper(
      <FindingActionWizard
        slug="acme"
        findings={[...findings]}
        onClose={onClose}
        onFindingCreated={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /finding group/i }));
    fireEvent.change(screen.getByPlaceholderText(/internet-facing/i), {
      target: { value: "Related exposure" },
    });
    fireEvent.change(screen.getByPlaceholderText(/explain why/i), {
      target: { value: "Same attack path" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Choose Findings" }));
    expect(screen.getByRole("button", { name: "Review group" })).toBeDisabled();
    fireEvent.click(screen.getByText("Alpha Finding"));
    fireEvent.click(screen.getByText("Beta Finding"));
    fireEvent.click(screen.getByRole("button", { name: "Review group" }));
    expect(screen.getByText(/does not merge, hide, delete/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create group" }));

    await waitFor(() => expect(createGroupMock).toHaveBeenCalled());
    expect(createGroupMock.mock.calls[0]?.[1]).toMatchObject({
      name: "Related exposure",
      rationale: "Same attack path",
      finding_ids: ["finding-alpha", "finding-beta"],
    });
    expect(createGroupMock.mock.calls[0]?.[1].idempotency_key).toBeTruthy();
    expect(onClose).toHaveBeenCalled();
  });

  it("preserves an edit draft when the server reports a stale version", async () => {
    const group = {
      id: "group-1",
      engagement_id: "engagement-1",
      name: "Original group",
      rationale: "Original rationale",
      created_by_user_id: null,
      row_version: 2,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      members: findings.map((finding, sortOrder) => ({
        finding_id: finding.id,
        sort_order: sortOrder,
        available: true,
        finding,
      })),
      rollup: {
        member_count: 2,
        available_members: 2,
        unavailable_members: 0,
        max_severity: "high" as const,
        status_counts: {},
        excluded_count: 0,
      },
    };
    useFindingGroupsMock.mockReturnValue({
      data: [group],
      isLoading: false,
      error: null,
    });
    updateGroupMock.mockRejectedValue(
      new Error("Finding group changed since it was loaded"),
    );
    wrapper(<FindingGroupsPanel slug="acme" findings={findings} canWrite />);

    fireEvent.click(screen.getByRole("button", { name: /original group/i }));
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByDisplayValue("Original group"), {
      target: { value: "Unsaved analyst draft" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Choose Findings" }));
    fireEvent.click(screen.getByRole("button", { name: "Review group" }));
    fireEvent.click(screen.getByRole("button", { name: "Save group" }));

    expect(await screen.findByRole("button", { name: "Reload latest" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /1\. details/i }));
    expect(screen.getByDisplayValue("Unsaved analyst draft")).toBeInTheDocument();
  });

  it("shows groups to guests without management controls", () => {
    useFindingGroupsMock.mockReturnValue({
      data: [
        {
          id: "group-1",
          engagement_id: "engagement-1",
          name: "Read-only group",
          rationale: "Shared narrative",
          created_by_user_id: null,
          row_version: 1,
          created_at: "2026-07-27T00:00:00Z",
          updated_at: "2026-07-27T00:00:00Z",
          members: [
            {
              finding_id: findings[0]!.id,
              sort_order: 0,
              available: false,
              finding: { ...findings[0]!, title: "Unavailable Finding" },
            },
          ],
          rollup: {
            member_count: 1,
            available_members: 0,
            unavailable_members: 1,
            max_severity: "info",
            status_counts: {},
            excluded_count: 0,
          },
        },
      ],
      isLoading: false,
      error: null,
    });
    wrapper(<FindingGroupsPanel slug="acme" findings={[]} canWrite={false} />);
    fireEvent.click(screen.getByRole("button", { name: /read-only group/i }));
    expect(screen.getByText(/unavailable historical member/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /unavailable finding/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /dissolve/i })).not.toBeInTheDocument();
  });
});
