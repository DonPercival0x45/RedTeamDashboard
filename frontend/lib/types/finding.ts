// Wire-format types for findings, attachments, and correlated entities.

export type Severity = "info" | "low" | "medium" | "high" | "critical";

export type FindingPhase =
  | "osint"
  | "vuln_scan"
  | "exploit"
  | "phishing"
  | "general";

export type FindingValidationStatus =
  | "pending_validation"
  | "validated"
  | "rejected"
  | "false_positive";

// Persisted finding as returned by GET /projects/{slug}/findings. Mirrors
// the SSE `finding.created` event's tool/args/data so the table can render
// hydrated and live findings the same way.
export interface Finding {
  id: string;
  thread_id: string | null;
  tool: string | null;
  target: string | null;
  args: Record<string, unknown>;
  data: Record<string, unknown>;
  severity: Severity;
  title: string;
  summary?: string | null;
  phase: FindingPhase;
  status: FindingValidationStatus;
  validated_at: string | null;
  created_at: string;
}

// Payload for POST /projects/{slug}/findings/import
export interface FindingImport {
  title: string;
  severity?: Severity;
  phase?: FindingPhase;
  summary?: string;
  target?: string;
  source_tool?: string;
  details?: Record<string, unknown>;
}

// Attachment metadata (raw bytes fetched separately via GET /attachments/{id})
export interface Attachment {
  id: string;
  finding_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
}

export type EntityType =
  | "email"
  | "ip"
  | "cidr"
  | "domain"
  | "subdomain"
  | "url"
  | "host";

export interface EntityFindingRef {
  id: string;
  title: string;
  tool: string | null;
  severity: Severity;
  phase: FindingPhase;
}

// Correlated entity derived from findings (GET /projects/{slug}/entities).
export interface Entity {
  type: string;
  value: string;
  count: number;
  severity: Severity;
  first_seen: string;
  last_seen: string;
  findings: EntityFindingRef[];
}
