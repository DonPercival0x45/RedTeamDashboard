// MSAL singleton + token acquisition usable outside React (api.ts/events.ts
// need a token without hooks). The instance only exists when Entra is
// configured; in dev mode every export is a harmless no-op.

import {
  BrowserAuthError,
  InteractionRequiredAuthError,
  type AccountInfo,
  PublicClientApplication,
} from "@azure/msal-browser";
import { ENTRA, ENTRA_ENABLED } from "@/lib/config";

export const msalInstance: PublicClientApplication | null = ENTRA_ENABLED
  ? new PublicClientApplication({
      auth: {
        clientId: ENTRA.clientId,
        authority: `https://login.microsoftonline.com/${ENTRA.tenantId}`,
        redirectUri:
          typeof window !== "undefined" ? window.location.origin : undefined,
      },
      cache: { cacheLocation: "localStorage" },
      // Mobile Safari + browsers with third-party cookies blocked can't
      // load Microsoft's silent-renew iframe in time and the default 6s
      // monitor window times out. Bumping to 20s reduces the rate of
      // `monitor_window_timeout` errors before we fall back to redirect.
      system: {
        iframeHashTimeout: 20000,
        windowHashTimeout: 20000,
        loadFrameTimeout: 20000,
      },
    })
  : null;

const SCOPES = ENTRA.apiScope ? [ENTRA.apiScope] : [];

// msal-browser v3 requires initialize() before any other call; memoize it.
let initPromise: Promise<void> | null = null;
export function ensureMsalReady(): Promise<void> {
  if (!msalInstance) return Promise.resolve();
  if (!initPromise) initPromise = msalInstance.initialize();
  return initPromise;
}

export function activeAccount(): AccountInfo | null {
  if (!msalInstance) return null;
  return (
    msalInstance.getActiveAccount() ?? msalInstance.getAllAccounts()[0] ?? null
  );
}

// Acquire an API access token. Strategy (v0.7.1):
//   1. Try acquireTokenSilent — the happy path (cached refresh token still
//      good).
//   2. On any silent failure (InteractionRequired, monitor_window_timeout,
//      any other BrowserAuthError) try acquireTokenPopup. Popup preserves
//      the page (no reload) so the in-flight click that triggered the
//      token acquisition can resume against the same API call with a fresh
//      token. This is the fix for the v0.7.0 "401 X-API-Key required"
//      production bug: silent failure used to fall straight through to
//      redirect, which navigated away ASYNCHRONOUSLY — meanwhile the JS
//      continued, sent an unauthenticated fetch, and the user saw the
//      backend's "no auth header" 401 before the redirect even fired.
//   3. If popup itself fails (blocked, dismissed, errored), fall back to
//      redirect as last resort — loses the in-flight request but the
//      analyst gets back into a valid session.
//
// Returns null when Entra is disabled OR when redirect was kicked off
// (caller must handle null by aborting whatever it was about to do —
// authHeaders() converts null into a thrown error so callers can't
// accidentally fire unauthenticated requests).
export async function getAccessToken(): Promise<string | null> {
  if (!msalInstance) return null;
  await ensureMsalReady();
  const account = activeAccount();
  if (!account) {
    await msalInstance.loginRedirect({ scopes: SCOPES });
    return null;
  }
  try {
    const result = await msalInstance.acquireTokenSilent({
      account,
      scopes: SCOPES,
    });
    return result.accessToken;
  } catch (err) {
    const isMonitorTimeout =
      err instanceof BrowserAuthError &&
      err.errorCode === "monitor_window_timeout";
    const interactiveNeeded =
      err instanceof InteractionRequiredAuthError ||
      isMonitorTimeout ||
      err instanceof BrowserAuthError;
    if (!interactiveNeeded) throw err;

    // Try popup first — preserves page state.
    try {
      const result = await msalInstance.acquireTokenPopup({
        account,
        scopes: SCOPES,
      });
      return result.accessToken;
    } catch {
      // Popup blocked / dismissed / errored. Fall back to redirect; the
      // caller sees null and authHeaders() throws so the in-flight
      // request aborts before the navigation lands.
      await msalInstance.acquireTokenRedirect({ scopes: SCOPES });
      return null;
    }
  }
}
