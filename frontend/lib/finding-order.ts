import type { Finding } from "@/lib/types";

const SEVERITY_RANK: Record<Finding["severity"], number> = {
  info: 0,
  low: 1,
  medium: 2,
  high: 3,
  critical: 4,
};

export function findingNeedsValidation(finding: Finding): boolean {
  return (
    finding.status === "pending_validation" || finding.status === "needs_review"
  );
}

/** Order the analyst queue before already-decided records. */
export function compareValidationPriority(a: Finding, b: Finding): number {
  const aNeedsValidation = findingNeedsValidation(a);
  const bNeedsValidation = findingNeedsValidation(b);
  if (aNeedsValidation !== bNeedsValidation) {
    return bNeedsValidation ? 1 : -1;
  }
  const severity = SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity];
  if (severity !== 0) return severity;
  return b.created_at.localeCompare(a.created_at);
}
