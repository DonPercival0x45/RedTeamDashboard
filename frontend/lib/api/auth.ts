// Approval gates, standing authorizations, and BYO provider keys.

import { request } from "./base";
import type {
  Approval,
  ApprovalStatus,
  Authorization,
  ProviderKey,
  ProviderKeyEntry,
  ProviderKeyImportPayload,
  ProviderKeyImportResult,
} from "@/lib/types/auth";

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

export function listApprovals(
  slug: string,
  status?: ApprovalStatus,
): Promise<Approval[]> {
  const q = status ? `?status=${status}` : "";
  return request<Approval[]>(`/projects/${slug}/approvals${q}`);
}

export function decideApproval(
  approvalId: string,
  body: {
    approved: boolean;
    edited_args?: Record<string, unknown>;
    reason?: string;
    remember_for_session?: boolean;
  },
): Promise<Approval> {
  return request<Approval>(`/approvals/${approvalId}/decision`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Authorizations
// ---------------------------------------------------------------------------

export function listAuthorizations(
  engagementId: string,
  active?: boolean,
): Promise<Authorization[]> {
  const q = active === undefined ? "" : `?active=${active}`;
  return request<Authorization[]>(
    `/projects/${engagementId}/authorizations${q}`,
  );
}

export function revokeAuthorization(
  authorizationId: string,
): Promise<Authorization> {
  return request<Authorization>(`/authorizations/${authorizationId}/revoke`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// BYO provider keys (LLM + MCP)
// ---------------------------------------------------------------------------

export function listProviderKeys(): Promise<ProviderKey[]> {
  return request<ProviderKey[]>("/me/provider-keys");
}

export function createProviderKey(body: ProviderKeyEntry): Promise<ProviderKey> {
  return request<ProviderKey>("/me/provider-keys", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function importProviderKeys(
  payload: ProviderKeyImportPayload,
): Promise<ProviderKeyImportResult> {
  return request<ProviderKeyImportResult>("/me/provider-keys/import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteProviderKey(keyId: string): Promise<void> {
  return request<void>(`/me/provider-keys/${keyId}`, { method: "DELETE" });
}

export function updateProviderKey(
  keyId: string,
  body: Partial<ProviderKeyEntry>,
): Promise<ProviderKey> {
  return request<ProviderKey>(`/me/provider-keys/${keyId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
