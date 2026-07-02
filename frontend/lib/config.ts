// Runtime configuration (v1.0.0+).
//
// v0.x shipped as a Static Web App with values inlined at build time via
// NEXT_PUBLIC_* env vars. v1.0.0 flips the frontend to a Container App
// running Node/Next.js so one image can serve any environment. Values are
// resolved fresh per-request on the server, then injected into the SSR HTML
// as `window.__RTD_CONFIG__` for the client to pick up.
//
// Read paths:
//   - Server (SSR, route handlers, RSC):  reads process.env directly.
//   - Client (in the browser):            reads the injected window global.
//
// `NEXT_PUBLIC_*` is still honored on the SERVER as a dev-mode fallback so
// existing `.env.local` files keep working with `next dev`.

interface RuntimeConfig {
  apiBaseUrl: string;
  entraTenantId: string;
  entraClientId: string;
  entraApiScope: string;
  devUser: string;
}

const DEV_FALLBACK: RuntimeConfig = {
  apiBaseUrl: "http://localhost:8000",
  entraTenantId: "",
  entraClientId: "",
  entraApiScope: "",
  devUser: "analyst@localhost",
};

export function readServerConfig(): RuntimeConfig {
  return {
    apiBaseUrl:
      process.env.RTD_API_BASE_URL ??
      process.env.NEXT_PUBLIC_API_BASE_URL ??
      DEV_FALLBACK.apiBaseUrl,
    entraTenantId:
      process.env.RTD_ENTRA_TENANT_ID ??
      process.env.NEXT_PUBLIC_ENTRA_TENANT_ID ??
      "",
    entraClientId:
      process.env.RTD_ENTRA_CLIENT_ID ??
      process.env.NEXT_PUBLIC_ENTRA_CLIENT_ID ??
      "",
    entraApiScope:
      process.env.RTD_ENTRA_API_SCOPE ??
      process.env.NEXT_PUBLIC_ENTRA_API_SCOPE ??
      "",
    devUser:
      process.env.RTD_DEV_USER ??
      process.env.NEXT_PUBLIC_DEV_USER ??
      DEV_FALLBACK.devUser,
  };
}

function readClientConfig(): RuntimeConfig {
  // Preferred: SSR-injected runtime config (v1.0.0 Container App path).
  const injected = (window as unknown as {
    __RTD_CONFIG__?: RuntimeConfig;
  }).__RTD_CONFIG__;
  if (injected) return injected;
  // Fallback: build-inlined NEXT_PUBLIC_* vars. Kept live during the v0.x→v1.x
  // parallel week so the old SWA (static export, no server-side render) can
  // still boot. Once the SWA is decommissioned this branch is dead code and
  // can be dropped.
  return {
    apiBaseUrl:
      process.env.NEXT_PUBLIC_API_BASE_URL ?? DEV_FALLBACK.apiBaseUrl,
    entraTenantId: process.env.NEXT_PUBLIC_ENTRA_TENANT_ID ?? "",
    entraClientId: process.env.NEXT_PUBLIC_ENTRA_CLIENT_ID ?? "",
    entraApiScope: process.env.NEXT_PUBLIC_ENTRA_API_SCOPE ?? "",
    devUser: process.env.NEXT_PUBLIC_DEV_USER ?? DEV_FALLBACK.devUser,
  };
}

function resolveConfig(): RuntimeConfig {
  return typeof window === "undefined" ? readServerConfig() : readClientConfig();
}

// Resolved once on first import per environment (module init on the server,
// hydration on the client). If something needs a fresh read (should be
// nothing in practice), call resolveConfig() directly.
const cfg = resolveConfig();

export const API_BASE_URL = cfg.apiBaseUrl;

export const ENTRA = {
  tenantId: cfg.entraTenantId,
  clientId: cfg.entraClientId,
  apiScope: cfg.entraApiScope,
};

export const ENTRA_ENABLED = Boolean(
  ENTRA.tenantId && ENTRA.clientId && ENTRA.apiScope,
);

export const DEV_USER = cfg.devUser;

// Global-window shape so consumers/tests can type-assert.
export const RUNTIME_CONFIG_WINDOW_KEY = "__RTD_CONFIG__" as const;
