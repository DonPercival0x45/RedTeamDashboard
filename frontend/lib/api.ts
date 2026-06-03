// Read-only fetch wrappers over the FastAPI surface.
//
// Phase 6: viewer is presentation-only. Every call takes an explicit Source
// (URL + API key) — there is no shared base URL, no implicit user. Mutation
// surfaces (engagement create/archive/flush, scope add/delete, run start,
// approval decide, grant revoke) all live in the CLI now and are gone from
// the viewer entirely.

import type { Source } from "@/lib/sources";
import type {
  Approval,
  ApprovalStatus,
  Authorization,
  Engagement,
  EngagementStatus,
  Finding,
} from "@/lib/types";

function headers(source: Source, extra?: HeadersInit): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-API-Key": source.apiKey,
    ...extra,
  };
}

async function request<T>(
  source: Source,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${source.url}${path}`, {
    ...init,
    headers: { ...headers(source), ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Engagements
// ---------------------------------------------------------------------------

export function listEngagements(
  source: Source,
  status?: EngagementStatus,
): Promise<Engagement[]> {
  const q = status ? `?status=${status}` : "";
  return request<Engagement[]>(source, `/engagements${q}`);
}

export function getEngagement(
  source: Source,
  slug: string,
): Promise<Engagement> {
  return request<Engagement>(source, `/engagements/${slug}`);
}

// ---------------------------------------------------------------------------
// Scope (read-only)
// ---------------------------------------------------------------------------

export function listScope(source: Source, slug: string) {
  return request<import("@/lib/types").ScopeItem[]>(
    source,
    `/engagements/${slug}/scope`,
  );
}

// ---------------------------------------------------------------------------
// Findings
// ---------------------------------------------------------------------------

export function listFindings(source: Source, slug: string): Promise<Finding[]> {
  return request<Finding[]>(source, `/engagements/${slug}/findings`);
}

// ---------------------------------------------------------------------------
// Approvals (read-only — decisions go through `rtd approve`)
// ---------------------------------------------------------------------------

export function listApprovals(
  source: Source,
  slug: string,
  status?: ApprovalStatus,
): Promise<Approval[]> {
  const q = status ? `?status=${status}` : "";
  return request<Approval[]>(source, `/engagements/${slug}/approvals${q}`);
}

// ---------------------------------------------------------------------------
// Authorizations (read-only — revoke goes through `rtd grants revoke`)
// ---------------------------------------------------------------------------

export function listAuthorizations(
  source: Source,
  engagementId: string,
  active?: boolean,
): Promise<Authorization[]> {
  const q = active === undefined ? "" : `?active=${active}`;
  return request<Authorization[]>(
    source,
    `/engagements/${engagementId}/authorizations${q}`,
  );
}

// ---------------------------------------------------------------------------
// Reports (read-only export — produces a PDF, no state mutation)
// ---------------------------------------------------------------------------

export async function downloadEngagementReport(
  source: Source,
  slug: string,
): Promise<void> {
  const response = await fetch(`${source.url}/engagements/${slug}/report`, {
    headers: { "X-API-Key": source.apiKey },
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  const blob = await response.blob();
  const filename =
    _filenameFromDisposition(response.headers.get("content-disposition")) ??
    `${slug}-report.pdf`;
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

function _filenameFromDisposition(value: string | null): string | null {
  if (!value) return null;
  const match = /filename="?([^"]+)"?/i.exec(value);
  return match ? match[1] : null;
}
