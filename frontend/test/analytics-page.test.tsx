import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("recharts", () => {
  const Container = ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  );
  const Empty = () => null;
  return {
    Bar: Empty,
    BarChart: Container,
    CartesianGrid: Empty,
    Cell: Empty,
    Line: Empty,
    LineChart: Container,
    Pie: Empty,
    PieChart: Container,
    ResponsiveContainer: Container,
    Tooltip: Empty,
    XAxis: Empty,
    YAxis: Empty,
  };
});

vi.mock("@/lib/hooks", () => ({
  useAnalyticsEngagementLog: vi.fn(),
  useAnalyticsFindingsOverTime: vi.fn(),
  useAnalyticsScanCoverage: vi.fn(),
  useAnalyticsSeverityBreakdown: vi.fn(),
  useAnalyticsTopFindings: vi.fn(),
  useEngagements: vi.fn(),
}));

import AnalyticsPage from "@/app/analytics/page";
import {
  useAnalyticsEngagementLog,
  useAnalyticsFindingsOverTime,
  useAnalyticsScanCoverage,
  useAnalyticsSeverityBreakdown,
  useAnalyticsTopFindings,
  useEngagements,
} from "@/lib/hooks";

const query = (data: unknown, error: unknown = null) => ({
  data,
  error,
  isLoading: false,
  isFetching: false,
  refetch: vi.fn(),
});

const engagement = {
  slug: "acme",
  name: "Acme",
  intelligence_architecture: "v3" as const,
};

function mockAnalytics({
  overTime = [],
  severity = [],
  coverage = null,
  topFindings = [],
  log = [],
  overTimeError = null,
}: {
  overTime?: unknown;
  severity?: unknown;
  coverage?: unknown;
  topFindings?: unknown;
  log?: unknown;
  overTimeError?: unknown;
} = {}) {
  vi.mocked(useEngagements).mockReturnValue(query([engagement]) as never);
  vi.mocked(useAnalyticsFindingsOverTime).mockReturnValue(
    query(overTime, overTimeError) as never,
  );
  vi.mocked(useAnalyticsSeverityBreakdown).mockReturnValue(
    query(severity) as never,
  );
  vi.mocked(useAnalyticsScanCoverage).mockReturnValue(
    query(coverage) as never,
  );
  vi.mocked(useAnalyticsTopFindings).mockReturnValue(
    query(topFindings) as never,
  );
  vi.mocked(useAnalyticsEngagementLog).mockReturnValue(query(log) as never);
}

describe("AnalyticsPage query states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not present failed analytics queries as empty results", () => {
    const failure = new Error("offline");
    vi.mocked(useEngagements).mockReturnValue(query([engagement]) as never);
    vi.mocked(useAnalyticsFindingsOverTime).mockReturnValue(
      query(undefined, failure) as never,
    );
    vi.mocked(useAnalyticsSeverityBreakdown).mockReturnValue(
      query(undefined, failure) as never,
    );
    vi.mocked(useAnalyticsScanCoverage).mockReturnValue(
      query(undefined, failure) as never,
    );
    vi.mocked(useAnalyticsTopFindings).mockReturnValue(
      query(undefined, failure) as never,
    );
    vi.mocked(useAnalyticsEngagementLog).mockReturnValue(
      query(undefined, failure) as never,
    );

    render(<AnalyticsPage />);

    expect(screen.getByText("Could not load findings over time.")).toBeInTheDocument();
    expect(screen.getByText("Could not load severity breakdown.")).toBeInTheDocument();
    expect(screen.getByText("Could not load scan coverage.")).toBeInTheDocument();
    expect(screen.getByText("Could not load top findings.")).toBeInTheDocument();
    expect(screen.getByText("Could not load engagement log.")).toBeInTheDocument();
    expect(screen.queryByText("No data.")).not.toBeInTheDocument();
    expect(screen.queryByText("No findings yet.")).not.toBeInTheDocument();
    expect(screen.queryByText("No engagement activity yet.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeDisabled();
  });

  it("enables export for complete, legitimately empty analytics", () => {
    mockAnalytics();

    render(<AnalyticsPage />);

    expect(screen.getByText("No data.")).toBeInTheDocument();
    expect(screen.getByText("No findings yet.")).toBeInTheDocument();
    expect(screen.getByText("No engagement activity yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeEnabled();
  });

  it("keeps cached results visible but disables export after refresh failure", () => {
    mockAnalytics({
      overTime: [{ label: "2026-W29", count: 4 }],
      overTimeError: new Error("refresh failed"),
    });

    render(<AnalyticsPage />);

    expect(
      screen.getByText("Refresh failed; showing cached data."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Findings over time" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeDisabled();
  });
});
