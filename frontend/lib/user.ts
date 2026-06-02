// X-User-Id helper: kept in localStorage so the same UUID/email persists
// across page loads. Real auth (Entra OIDC) replaces this seam later.

const KEY = "rtd.user_id";

export function getUserId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(KEY);
}

export function setUserId(value: string): void {
  window.localStorage.setItem(KEY, value.trim());
}

export function clearUserId(): void {
  window.localStorage.removeItem(KEY);
}
