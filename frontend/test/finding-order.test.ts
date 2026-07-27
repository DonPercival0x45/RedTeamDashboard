import { describe, expect, it } from "vitest";
import {
  compareValidationPriority,
  findingNeedsValidation,
} from "@/lib/finding-order";
import type { Finding } from "@/lib/types";

function finding(
  id: string,
  status: Finding["status"],
  severity: Finding["severity"],
  createdAt: string,
): Finding {
  return {
    id,
    thread_id: null,
    tool: "test",
    target: null,
    args: {},
    data: {},
    severity,
    title: id,
    phase: "general",
    status,
    validated_at: null,
    observed_at: null,
    burp_serial_number: null,
    created_at: createdAt,
  };
}

describe("finding validation ordering", () => {
  it("treats pending and needs-review findings as analyst work", () => {
    expect(
      findingNeedsValidation(
        finding("pending", "pending_validation", "low", "2026-01-01T00:00:00Z"),
      ),
    ).toBe(true);
    expect(
      findingNeedsValidation(
        finding("review", "needs_review", "low", "2026-01-01T00:00:00Z"),
      ),
    ).toBe(true);
    expect(
      findingNeedsValidation(
        finding("validated", "validated", "critical", "2026-01-01T00:00:00Z"),
      ),
    ).toBe(false);
  });

  it("orders validation work before decided findings, then by severity", () => {
    const rows = [
      finding("validated-critical", "validated", "critical", "2026-01-03T00:00:00Z"),
      finding("pending-low", "pending_validation", "low", "2026-01-02T00:00:00Z"),
      finding("review-high", "needs_review", "high", "2026-01-01T00:00:00Z"),
    ];

    expect(rows.sort(compareValidationPriority).map((row) => row.id)).toEqual([
      "review-high",
      "pending-low",
      "validated-critical",
    ]);
  });
});
