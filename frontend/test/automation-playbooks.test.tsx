import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const replace = vi.fn();
const state = vi.hoisted(() => ({ engagementError: null as Error | null }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => new URLSearchParams("tab=playbooks&slug=acme"),
}));

vi.mock("@/lib/hooks", () => ({
  useEngagements: () => ({
    data: state.engagementError
      ? undefined
      : [{ name: "Acme", slug: "acme", status: "active" }],
    isLoading: false,
    isFetching: false,
    error: state.engagementError,
    refetch: vi.fn(),
  }),
  useMe: () => ({ data: { role: "user" } }),
  useRunningJobs: () => ({ data: [] }),
}));

vi.mock("@/components/playbooks/playbooks-tab", () => ({
  PlaybooksTab: ({
    engagementSlug,
    showCreateAction,
  }: {
    engagementSlug: string;
    showCreateAction?: boolean;
  }) => (
    <div>
      Runs for {engagementSlug}; inline create: {String(showCreateAction)}
    </div>
  ),
}));

vi.mock("@/components/playbooks/playbook-editor-modal", () => ({
  PlaybookEditorModal: () => <div>Global playbook editor</div>,
}));

vi.mock("@/components/report-builder", () => ({ ReportBuilder: () => null }));

import AutomationPage from "@/app/automation/page";

describe("Automation playbook catalog placement", () => {
  it("keeps global authoring separate from engagement run context", () => {
    state.engagementError = null;
    render(<AutomationPage />);

    const add = screen.getByRole("button", { name: "Add a playbook" });
    const engagement = screen.getByText("Run in engagement");
    expect(
      add.compareDocumentPosition(engagement) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      screen.getByText("Runs for acme; inline create: false"),
    ).toBeInTheDocument();

    fireEvent.click(add);
    expect(screen.getByText("Global playbook editor")).toBeInTheDocument();
  });

  it("keeps global authoring available when engagement contexts fail", () => {
    state.engagementError = new Error("engagements unavailable");
    render(<AutomationPage />);

    expect(screen.getByText("Shared playbook catalog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add a playbook" }));
    expect(screen.getByText("Global playbook editor")).toBeInTheDocument();
    expect(screen.getByText(/could not load engagement run contexts/i)).toBeInTheDocument();
  });
});
