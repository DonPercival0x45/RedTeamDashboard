import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/dynamic", () => ({
  default: () => () => <div data-testid="map" />,
}));

vi.mock("@/lib/hooks", () => ({
  qk: {
    engagements: () => ["engagements"],
    entities: (slug: string) => ["entities", slug],
    entityDuplicateCandidates: (slug: string) => ["entity-duplicates", slug],
    scope: (slug: string) => ["scope", slug],
    storedEntities: (slug: string) => ["stored-entities", slug],
  },
  useEntities: vi.fn(),
  useEntityDuplicateCandidates: vi.fn(),
  useFindings: vi.fn(),
  useScope: vi.fn(),
  useStoredEntities: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    deleteScopeItem: vi.fn(),
    importScope: vi.fn(),
  };
});

import { EntitiesView } from "@/components/entities-view";
import { deleteScopeItem, importScope } from "@/lib/api";
import {
  useEntities,
  useEntityDuplicateCandidates,
  useFindings,
  useScope,
  useStoredEntities,
} from "@/lib/hooks";

const query = (data: unknown, error: unknown = null) => ({
  data,
  error,
  isLoading: data === undefined && !error,
  isFetching: false,
  refetch: vi.fn(),
});

function renderView(canWrite = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <EntitiesView slug="acme" canWrite={canWrite} />
    </QueryClientProvider>,
  );
}

describe("EntitiesView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useEntities).mockReturnValue(
      query([
        {
          type: "domain",
          value: "scope.example",
          count: 0,
          severity: "info",
          first_seen: "2026-07-24T00:00:00Z",
          last_seen: "2026-07-24T00:00:00Z",
          findings: [],
          scope_status: "live",
        },
      ]) as never,
    );
    vi.mocked(useFindings).mockReturnValue(query([]) as never);
    vi.mocked(useScope).mockReturnValue(query([]) as never);
    vi.mocked(useStoredEntities).mockReturnValue(query([]) as never);
    vi.mocked(useEntityDuplicateCandidates).mockReturnValue(query([]) as never);
    vi.mocked(importScope).mockResolvedValue({
      created: [],
      duplicates: [],
      errors: [],
    });
    vi.mocked(deleteScopeItem).mockResolvedValue(undefined);
  });

  it("opens a keyboard-accessible Findings-style preview for a scope entity", async () => {
    renderView(false);

    const trigger = screen.getByRole("button", { name: "scope.example" });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("scope.example");
    expect(dialog).toHaveTextContent("Entity preview with scope status");
    expect(dialog).toHaveTextContent("no finding has referenced it yet");
    expect(
      screen.getByRole("link", { name: "Open full entity view" }),
    ).toHaveAttribute(
      "href",
      "/e/entities?slug=acme&type=domain&value=scope.example",
    );

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("keeps guest entity management read-only", () => {
    renderView(false);

    expect(screen.getByText("Read-only")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Import" })).not.toBeInTheDocument();
  });

  it("shows retryable load failure without a false empty inventory", () => {
    vi.mocked(useEntities).mockReturnValue(
      query(undefined, new Error("offline")) as never,
    );

    renderView(true);

    expect(
      screen.getByText("Could not load scope and discovered entities."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(
      screen.queryByText("No entities yet — add scope or run a collection playbook."),
    ).not.toBeInTheDocument();
  });

  it("keeps import available to writable analysts", () => {
    renderView(true);
    expect(screen.getByRole("button", { name: "Import" })).toBeInTheDocument();
  });

  it("bulk-selects visible scope-compatible entities and adds them with found provenance", async () => {
    vi.mocked(useEntities).mockReturnValue(
      query([
        {
          type: "domain",
          value: "new.example",
          count: 1,
          severity: "low",
          first_seen: "2026-07-24T00:00:00Z",
          last_seen: "2026-07-24T00:00:00Z",
          findings: [],
          scope_status: "oos",
        },
        {
          type: "subdomain",
          value: "legacy.example",
          count: 1,
          severity: "info",
          first_seen: "2026-07-24T00:00:00Z",
          last_seen: "2026-07-24T00:00:00Z",
          findings: [],
          scope_status: "legacy",
        },
        {
          type: "email",
          value: "person@example.com",
          count: 1,
          severity: "info",
          first_seen: "2026-07-24T00:00:00Z",
          last_seen: "2026-07-24T00:00:00Z",
          findings: [],
          scope_status: "oos",
        },
      ]) as never,
    );
    renderView(true);

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Select all visible scope-compatible entities",
      }),
    );
    expect(screen.getByRole("checkbox", { name: "Select new.example" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select legacy.example" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select person@example.com" })).toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: "Add to scope" }));
    await waitFor(() =>
      expect(importScope).toHaveBeenCalledWith(
        "acme",
        "new.example\nlegacy.example\nperson@example.com",
        "found",
      ),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "3 entities added to scope",
    );
  });

  it("bulk-assigns selected entities to the exclusion tag", async () => {
    vi.mocked(useEntities).mockReturnValue(
      query([
        {
          type: "ip",
          value: "203.0.113.7",
          count: 1,
          severity: "info",
          first_seen: "2026-07-24T00:00:00Z",
          last_seen: "2026-07-24T00:00:00Z",
          findings: [],
          scope_status: "live",
        },
      ]) as never,
    );
    renderView(true);

    fireEvent.click(screen.getByRole("checkbox", { name: "Select 203.0.113.7" }));
    fireEvent.click(screen.getByRole("button", { name: "Exclude" }));

    await waitFor(() =>
      expect(importScope).toHaveBeenCalledWith("acme", "!203.0.113.7", "found"),
    );
  });

  it("lets an analyst change and remove exact scope rules from the entity dialog", async () => {
    vi.mocked(useEntities).mockReturnValue(
      query([
        {
          type: "domain",
          value: "blocked.example",
          count: 1,
          severity: "medium",
          first_seen: "2026-07-24T00:00:00Z",
          last_seen: "2026-07-24T00:00:00Z",
          findings: [],
          scope_status: "oos",
        },
      ]) as never,
    );
    vi.mocked(useScope).mockReturnValue(
      query([
        {
          id: "scope-exclusion",
          engagement_id: "engagement-id",
          kind: "domain",
          value: "blocked.example",
          is_exclusion: true,
          note: null,
          source: "defined",
          created_at: "2026-07-24T00:00:00Z",
          updated_at: "2026-07-24T00:00:00Z",
        },
      ]) as never,
    );
    renderView(true);

    fireEvent.click(
      screen.getByRole("button", { name: "Manage scope for blocked.example" }),
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Excluded · 1 exact rule/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Add to scope" }));
    await waitFor(() =>
      expect(importScope).toHaveBeenCalledWith("acme", "blocked.example", "found"),
    );
    await waitFor(() =>
      expect(deleteScopeItem).toHaveBeenCalledWith("acme", "scope-exclusion"),
    );
  });

  it("does not expose bulk or dialog scope mutations to guests", () => {
    renderView(false);
    expect(screen.queryByRole("checkbox", { name: /Select scope\.example/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add to scope" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Manage scope for scope.example" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Read-only")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Add to scope" })).not.toBeInTheDocument();
  });
});
