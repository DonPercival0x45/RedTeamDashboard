import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const createFromItem = vi.fn();
const hierarchy = {
  assets: [
    {
      id: "asset-ip",
      kind: "ip",
      canonical_key: "ip:192.0.2.10",
      label: "Service Detection: IP(192.0.2.10)",
      value: "192.0.2.10",
      ip: "192.0.2.10",
      hostname: null,
      protocol: null,
      port: null,
      service: null,
      url: null,
      finding_refs: [],
      rollup: {
        max_severity: "high",
        needs_review: 1,
        actionable: 0,
        inventory: 1,
        resolved_excluded: 0,
        distinct_findings: 2,
        latest_at: "2026-07-27T00:00:00Z",
      },
      create_finding_allowed: true,
      suggested_title: "Finding on IP(192.0.2.10)",
      suggested_target: "192.0.2.10",
      children: [
        {
          id: "service-443",
          kind: "service",
          canonical_key: "ip:192.0.2.10:service:tcp:443",
          label: "443/tcp · HTTPS · nginx 1.24",
          value: "192.0.2.10:443/tcp",
          ip: "192.0.2.10",
          hostname: null,
          protocol: "tcp",
          port: 443,
          service: "https",
          url: null,
          children: [],
          finding_refs: [
            {
              id: "source-finding",
              title: "Changed service fingerprint",
              tool: "service_detect",
              target: "192.0.2.10:443",
              severity: "high",
              phase: "vuln_scan",
              status: "needs_review",
              exclusion: null,
              observed_at: null,
              created_at: "2026-07-27T00:00:00Z",
              bucket: "needs_review",
            },
          ],
          rollup: {
            max_severity: "high",
            needs_review: 1,
            actionable: 0,
            inventory: 0,
            resolved_excluded: 0,
            distinct_findings: 1,
            latest_at: "2026-07-27T00:00:00Z",
          },
          create_finding_allowed: true,
          suggested_title: "HTTPS exposure on 192.0.2.10",
          suggested_target: "192.0.2.10:443",
        },
      ],
    },
  ],
  ungrouped: [],
  counts: {
    focus: 1,
    needs_review: 1,
    actionable: 0,
    inventory: 1,
    resolved_excluded: 0,
    distinct_findings: 2,
  },
  generated_at: "2026-07-27T00:00:00Z",
  projection_version: "finding-hierarchy-v1",
};

vi.mock("@/lib/hooks", () => ({
  qk: {
    findings: (slug: string) => ["findings", slug],
    findingHierarchy: (slug: string) => ["finding-hierarchy", slug],
    entities: (slug: string) => ["entities", slug],
  },
  useFindingHierarchy: () => ({
    data: hierarchy,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  createFindingFromHierarchyItem: (...args: unknown[]) => createFromItem(...args),
}));

import { FindingHierarchyWorkspace } from "@/components/finding-hierarchy-workspace";

function renderWorkspace(canWrite: boolean, onCreated = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FindingHierarchyWorkspace
        slug="acme"
        canWrite={canWrite}
        view="focus"
        onViewChange={vi.fn()}
        onCreated={onCreated}
        onUseClassic={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

describe("FindingHierarchyWorkspace", () => {
  beforeEach(() => createFromItem.mockReset());

  it("expands service inventory in place and hides mutations from guests", () => {
    renderWorkspace(false);
    fireEvent.click(screen.getByRole("button", { name: /expand service detection/i }));
    expect(screen.getByText(/443\/tcp · HTTPS · nginx 1.24/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create Finding" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Classic table" })).not.toBeInTheDocument();
  });

  it("binds duplicate override to the reviewed candidates and source target", async () => {
    const warning = {
      state: "duplicate_warning",
      finding: null,
      candidates: [
        {
          id: "existing",
          title: "Existing HTTPS Finding",
          target: "192.0.2.10:443",
          severity: "high",
          status: "validated",
          exclusion: null,
          match_reason: "same canonical affected target",
        },
      ],
    };
    createFromItem.mockResolvedValue(warning);
    renderWorkspace(true);
    fireEvent.click(screen.getByRole("button", { name: /expand service detection/i }));
    fireEvent.click(screen.getAllByRole("button", { name: "Create Finding" }).at(-1)!);
    expect(screen.getByDisplayValue("192.0.2.10:443")).toHaveAttribute("readonly");
    fireEvent.click(screen.getByRole("button", { name: "Create Finding" }));
    expect(await screen.findByText("Existing HTTPS Finding")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create anyway" }));
    await waitFor(() => expect(createFromItem).toHaveBeenCalledTimes(2));
    expect(createFromItem.mock.calls[1][1]).toMatchObject({
      target: "192.0.2.10:443",
      duplicate_decision: "create_anyway",
      reviewed_duplicate_ids: ["existing"],
    });
  });

  it("creates a pending Finding from a stable nested item", async () => {
    const onCreated = vi.fn();
    createFromItem.mockResolvedValue({
      state: "created",
      candidates: [],
      finding: {
        id: "promoted",
        thread_id: null,
        tool: "manual_promotion",
        target: "192.0.2.10:443",
        args: {},
        data: {},
        severity: "high",
        title: "HTTPS exposure on 192.0.2.10",
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
    });
    renderWorkspace(true, onCreated);
    fireEvent.click(screen.getByRole("button", { name: /expand service detection/i }));
    const createButtons = screen.getAllByRole("button", { name: "Create Finding" });
    fireEvent.click(createButtons.at(-1)!);
    expect(screen.getByText(/does not change scope/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create Finding" }));

    await waitFor(() => expect(createFromItem).toHaveBeenCalled());
    expect(createFromItem.mock.calls[0][1]).toMatchObject({
      item_id: "service-443",
      duplicate_decision: "review",
    });
    expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ id: "promoted" }));
  });
});
