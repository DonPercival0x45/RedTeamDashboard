import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useScope = vi.fn();
const runToolDirect = vi.fn();

vi.mock("@/lib/hooks", () => ({
  useScope: () => useScope(),
  qk: {
    findings: (slug: string) => ["findings", slug],
    entities: (slug: string) => ["entities", slug],
  },
}));

vi.mock("@/lib/api", () => ({
  runToolDirect: (...args: unknown[]) => runToolDirect(...args),
}));

import { V3ToolsPanel } from "@/components/v3-tools-panel";

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <V3ToolsPanel slug="alpha" />
    </QueryClientProvider>,
  );
}

const scope = [
  {
    id: "scope-1",
    engagement_id: "eng-1",
    kind: "domain",
    value: "foo.example",
    is_exclusion: false,
    note: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "scope-2",
    engagement_id: "eng-1",
    kind: "domain",
    value: "bar.example",
    is_exclusion: false,
    note: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "scope-3",
    engagement_id: "eng-1",
    kind: "domain",
    value: "blocked.example",
    is_exclusion: true,
    note: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

describe("V3ToolsPanel", () => {
  beforeEach(() => {
    useScope.mockReset();
    runToolDirect.mockReset();
    useScope.mockReturnValue({
      data: scope,
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    });
    runToolDirect.mockResolvedValue({
      ok: true,
      tool: "whois",
      scope: "foo.example",
      findings_new: 1,
      findings_total: 1,
      finding_id: "finding-1",
      stub: false,
      error: null,
      data: {},
    });
  });

  it("offers existing include scope and never offers exclusions", async () => {
    renderPanel();
    const picker = await screen.findByLabelText("Authorized target");
    expect(picker).toHaveTextContent("foo.example");
    expect(picker).toHaveTextContent("bar.example");
    expect(picker).not.toHaveTextContent("blocked.example");
  });

  it("runs the selected passive tool against the selected scope", async () => {
    renderPanel();
    const picker = await screen.findByLabelText("Authorized target");
    fireEvent.change(picker, { target: { value: "bar.example" } });
    fireEvent.click(screen.getByRole("button", { name: /WHOIS/i }));

    await waitFor(() =>
      expect(runToolDirect).toHaveBeenCalledWith("alpha", "whois", {
        scope: "bar.example",
      }),
    );
  });

  it("disables execution and gives guidance when scope is empty", () => {
    useScope.mockReturnValue({
      data: [],
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    });
    renderPanel();

    expect(
      screen.getByText("Add an included domain target in Scope before running a tool."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /WHOIS/i })).toBeDisabled();
  });

  it("shows a request failure instead of an empty-scope state", () => {
    useScope.mockReturnValue({
      data: undefined,
      error: new Error("offline"),
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    });
    renderPanel();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not load scope targets.",
    );
    expect(
      screen.queryByText("Add an included domain target in Scope before running a tool."),
    ).not.toBeInTheDocument();
  });
});
