import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QueryState, queryErrorMessage } from "@/components/query-state";

describe("QueryState", () => {
  it("renders loading without presenting an empty state", () => {
    render(<QueryState isLoading error={null} loadingLabel="Loading findings…" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading findings…");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders an actionable terminal error", () => {
    const retry = vi.fn();
    render(
      <QueryState
        isLoading={false}
        error={new Error("network offline")}
        errorLabel="Could not load findings."
        onRetry={retry}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load findings.");
    expect(screen.getByRole("alert")).toHaveTextContent("network offline");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("keeps stale data visible with a refresh warning", () => {
    render(
      <QueryState
        isLoading={false}
        error="timeout"
        hasData
        compact
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Refresh failed; showing cached data.",
    );
  });

  it("returns no UI for a successful query", () => {
    const { container } = render(<QueryState isLoading={false} error={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("normalizes unknown errors", () => {
    expect(queryErrorMessage({ code: 500 })).toBe("[object Object]");
    expect(queryErrorMessage(null, "fallback")).toBe("fallback");
  });
});
