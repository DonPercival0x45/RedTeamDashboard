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

// Acquire an API access token. Silent first; on interaction-required, kick off
// a redirect (which navigates away, so we return null). Returns null when
// Entra is disabled.
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
    // Three classes of "silent didn't work, ask the user":
    //   1. InteractionRequiredAuthError — consent / MFA / re-auth needed
    //   2. BrowserAuthError with monitor_window_timeout — the silent iframe
    //      couldn't load login.microsoftonline.com in time (mobile Safari,
    //      third-party cookies disabled, restrictive network)
    //   3. Anything else network/transient that prevented silent renewal
    // For all of them, fall back to an interactive redirect rather than
    // throwing into a "Loading…" wall.
    const isMonitorTimeout =
      err instanceof BrowserAuthError &&
      err.errorCode === "monitor_window_timeout";
    if (
      err instanceof InteractionRequiredAuthError ||
      isMonitorTimeout ||
      err instanceof BrowserAuthError
    ) {
      await msalInstance.acquireTokenRedirect({ scopes: SCOPES });
      return null;
    }
    throw err;
  }
}
