// Wire-format types for API keys, authorization gates, approvals, and
// BYO provider keys.

export type APIKeyScope = "viewer" | "cli" | "admin";

export interface APIKeyInfo {
  id: string;
  name: string;
  scope: APIKeyScope;
  created_by: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export type RiskLevel = "passive" | "active" | "destructive";

export type ApprovalStatus =
  | "pending"
  | "approved"
  | "denied"
  | "edited"
  | "auto";

// Per-(Project, tool) standing session grant. A row with revoked_at=null is
// active and the gate auto-approves matching active calls.
export interface Authorization {
  id: string;
  project_id: string;
  tool_name: string;
  granted_by: string | null;
  note: string | null;
  revoked_at: string | null;
  revoked_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface Approval {
  id: string;
  project_id: string;
  thread_id: string;
  node: string | null;
  tool_name: string;
  tool_args: Record<string, unknown>;
  risk: RiskLevel;
  scope_check: Record<string, unknown>;
  status: ApprovalStatus;
  decided_by: string | null;
  decision_args: Record<string, unknown> | null;
  authorization_id: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

export type ProviderKeyKind = "model_provider" | "mcp_server";

export interface ProviderKey {
  id: string;
  user_id: string;
  kind: ProviderKeyKind;
  name: string;
  provider: string;
  is_local: boolean;
  models: string[];
  endpoint: string | null;
  key_last4: string | null;
  extra: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ProviderKeyEntry {
  name: string;
  provider: string;
  kind?: ProviderKeyKind;
  models?: string[];
  is_local?: boolean;
  endpoint?: string | null;
  api_key?: string | null;
  extra?: Record<string, unknown>;
}

export interface ProviderKeyImportPayload {
  providers: ProviderKeyEntry[];
}

export interface ProviderKeyImportErrorRow {
  index: number;
  name: string | null;
  reason: string;
}

export interface ProviderKeyImportResult {
  created: ProviderKey[];
  errors: ProviderKeyImportErrorRow[];
  duplicates: ProviderKeyImportErrorRow[];
}
