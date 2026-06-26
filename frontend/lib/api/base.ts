// Shared fetch primitive and auth utilities used by all domain API modules.
//
// Phase 7: one backend (API_BASE_URL), identified analyst. Auth is resolved
// per request — an Entra Bearer token when SSO is configured, else a dev
// X-User-Id header for local work.

import { API_BASE_URL, DEV_USER, ENTRA_ENABLED } from "@/lib/config";
import { getAccessToken } from "@/lib/msal";

export { API_BASE_URL };

// Auth-only headers (no Content-Type — request() adds that for JSON bodies).
export async function authHeaders(): Promise<Record<string, string>> {
  if (ENTRA_ENABLED) {
    const token = await getAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }
  return { "X-User-Id": DEV_USER };
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(await authHeaders()),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
