"use client";

// Auth abstraction with two implementations chosen by a build-time constant
// (ENTRA_ENABLED) so hooks are never called conditionally:
//   - MsalAuthProvider: real Entra SSO (sign-in/out, identity from MSAL).
//   - DevAuthProvider:   a fixed dev identity, no sign-in (local dev).
// Components use useAuth() regardless of which is active.
//
// We drive @azure/msal-browser directly (no @azure/msal-react) — its needs
// here are small and msal-react's react peer range lags React 19.

import { createContext, useContext, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { flushMyProviderKeys } from "@/lib/api";
import { DEV_USER, ENTRA, ENTRA_ENABLED } from "@/lib/config";
import { activeAccount, ensureMsalReady, msalInstance } from "@/lib/msal";

export interface Identity {
  name: string;
  username: string;
}

interface AuthValue {
  ready: boolean;
  enabled: boolean;
  identity: Identity | null;
  signIn: () => void;
  signOut: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}

// ── Entra (MSAL) implementation ──────────────────────────────────────────

function MsalAuthProvider({ children }: { children: React.ReactNode }) {
  // msalInstance is non-null here (ENTRA_ENABLED gates the export selection).
  const instance = msalInstance!;
  const qc = useQueryClient();
  const [ready, setReady] = useState(false);
  const [identity, setIdentity] = useState<Identity | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await ensureMsalReady();
      // Capture the account returned from a login redirect, if any.
      const resp = await instance.handleRedirectPromise();
      if (resp?.account) {
        instance.setActiveAccount(resp.account);
      } else if (!instance.getActiveAccount() && instance.getAllAccounts()[0]) {
        instance.setActiveAccount(instance.getAllAccounts()[0]);
      }
      if (cancelled) return;
      const account = activeAccount();
      setIdentity(
        account
          ? { name: account.name ?? account.username, username: account.username }
          : null,
      );
      setReady(true);
      // v1.0.0 fix: any TanStack Query hook that fired during hydration
      // (before this effect finished) got a null token and errored. Reset
      // them all now that we have a real account — they'll refetch with
      // proper Bearer headers and the errored state clears.
      if (account) qc.resetQueries();
    })();
    return () => {
      cancelled = true;
    };
  }, [instance, qc]);

  const value: AuthValue = {
    ready,
    enabled: true,
    identity,
    signIn: () => {
      void instance.loginRedirect({ scopes: [ENTRA.apiScope] });
    },
    signOut: () => {
      // Wipe the ephemeral provider-keys cache before tearing down the
      // session. Best-effort — if the API call fails (network blip,
      // already expired), we still continue to logoutRedirect so the
      // Entra session ends.
      void flushMyProviderKeys()
        .catch(() => undefined)
        .finally(() => {
          void instance.logoutRedirect();
        });
    },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// ── Dev implementation (no tenant) ───────────────────────────────────────

function DevAuthProvider({ children }: { children: React.ReactNode }) {
  const value: AuthValue = {
    ready: true,
    enabled: false,
    identity: { name: DEV_USER, username: DEV_USER },
    signIn: () => {},
    signOut: () => {},
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const AuthProvider = ENTRA_ENABLED ? MsalAuthProvider : DevAuthProvider;
