// Wire-format types for projects (engagements), scope, and run streams.

import type { FindingPhase, FindingValidationStatus, Severity } from "./finding";
import type { RiskLevel } from "./auth";

export type EngagementStatus = "active" | "archived" | "flushed";
export type ScopeKind = "domain" | "cidr" | "ip" | "url";

export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  status: EngagementStatus;
  created_by: string | null;
  archived_at: string | null;
  flushed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScopeItem {
  id: string;
  project_id: string;
  kind: ScopeKind;
  value: string;
  is_exclusion: boolean;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScopeImportPreviewRow {
  line: number;
  value: string;
  kind: ScopeKind;
  is_exclusion: boolean;
}

export interface ScopeImportErrorRow {
  line: number;
  raw: string;
  reason: string;
}

export interface ScopeImportDuplicateRow {
  line: number;
  value: string;
  kind: ScopeKind;
  is_exclusion: boolean;
}

export interface ScopeImportPreview {
  preview: ScopeImportPreviewRow[];
  errors: ScopeImportErrorRow[];
  would_create: number;
}

export interface ScopeImportResult {
  created: ScopeItem[];
  errors: ScopeImportErrorRow[];
  duplicates: ScopeImportDuplicateRow[];
}

export type LLMProvider = "anthropic" | "openai" | "azure" | "ollama";

export interface RunModel {
  provider: LLMProvider;
  name: string;
}

export interface RunStartResponse {
  project_id: string;
  thread_id: string;
  events_stream: string;
  model: RunModel;
}

// SSE events emitted from the outbound run stream.
export type RunEvent =
  | { type: "run.started"; thread_id: string; prompt: string }
  | {
      type: "approval.pending";
      thread_id: string;
      approval_id: string;
      tool: string;
      args: Record<string, unknown>;
      risk: RiskLevel;
      scope: Record<string, unknown>;
      tool_call_id: string;
    }
  | {
      type: "tool.denied";
      thread_id: string;
      tool: string;
      args: Record<string, unknown>;
      reason: string;
      scope: Record<string, unknown>;
    }
  | {
      type: "tool.auto_approved";
      thread_id: string;
      tool: string;
      args: Record<string, unknown>;
      risk: string;
      authorization_id: string;
    }
  | {
      type: "finding.created";
      thread_id: string;
      tool: string;
      args: Record<string, unknown>;
      data: Record<string, unknown>;
      target: string | null;
      severity: Severity;
      title: string | null;
      finding_id: string;
      phase: FindingPhase;
      status: FindingValidationStatus;
    }
  | { type: "run.completed"; thread_id: string }
  | { type: "run.errored"; thread_id: string; error: string };

export type RunEventType = RunEvent["type"];
