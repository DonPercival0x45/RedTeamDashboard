import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/dynamic", () => ({
  default: () => () => <div data-testid="map" />,
}));

vi.mock("@/lib/hooks", () => ({
  qk: {
    entityDuplicateCandidates: (slug: string) => ["entity-duplicates", slug],
    storedEntities: (slug: string) => ["stored-entities", slug],
  },
  useEntities: vi.fn(),
  useEntityDuplicateCandidates: vi.fn(),
  useFindings: vi.fn(),
  useStoredEntities: vi.fn(),
}));

import { EntitiesView } from "@/components/entities-view";
import {
  useEntities,
  useEntityDuplicateCandidates,
  useFindings,
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
    vi.mocked(useStoredEntities).mockReturnValue(query([]) as never);
    vi.mocked(useEntityDuplicateCandidates).mockReturnValue(query([]) as never);
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
});
