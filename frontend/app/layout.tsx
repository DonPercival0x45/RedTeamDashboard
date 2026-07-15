import type { Metadata } from "next";
import { AuthGate } from "@/components/auth-gate";
import { QueryProvider } from "@/components/query-provider";
import { RunToastProvider } from "@/components/run-toast-provider";
import { AuthProvider } from "@/lib/auth";
import { readServerConfig, RUNTIME_CONFIG_WINDOW_KEY } from "@/lib/config";
import { themePreHydrationScript } from "@/lib/theme-preflight";
import { A11Y_PRE_HYDRATION_SCRIPT } from "@/lib/accessibility";
import { AppShell } from "@/components/app-shell/app-shell";
import pkg from "../package.json";
import "./globals.css";

// Force per-request rendering so the runtime env is read fresh on every load
// (Container Apps env vars can change between deployments; we don't want a
// build-time snapshot).
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Project XR@Y",
  description: "Project XR@Y — engagements, agents, findings, reporting",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Runtime config is read on the server per-request and inlined into the
  // HTML head, so the client picks it up before any module reads config.
  const runtimeConfig = readServerConfig();
  const runtimeConfigScript = `window.${RUNTIME_CONFIG_WINDOW_KEY} = ${JSON.stringify(
    runtimeConfig,
  )};`;

  // v1.8.0: theme selection lives in localStorage (see lib/themes.ts).
  // SSR default is `dark` — the pre-hydration script below stamps the
  // analyst's saved preference on <html> before React mounts to avoid a
  // theme flash. `.dark` stays on <html> too so any lingering `dark:`
  // Tailwind utility variants still resolve (harmless when data-theme
  // switches to light or high-contrast; the CSS variables win).
  //
  // v1.25.3: `suppressHydrationWarning` on <html> so React 19 doesn't
  // reconcile the pre-hydration script's attribute mutations.
  //
  // v2.0.0: the sticky top header is gone — LeftSidebar (rendered inside
  // AppShell below) owns brand + navigation + identity. Existing routes
  // (/e/*, /settings/*, /new) render inside AppShell's main region
  // unchanged; new top-level routes /engagements, /automation,
  // /analytics, /infrastructure live in app/*/page.tsx.
  return (
    <html
      lang="en"
      className="dark"
      data-theme="dark"
      suppressHydrationWarning
    >
      <head>
        <script
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{ __html: themePreHydrationScript() }}
        />
        <script
          // v1.25.0: stamp accessibility root attrs synchronously so
          // reduced-motion + colorblind + SR-hints preferences take
          // effect before React mounts (avoids a flash of animated
          // content or the default palette).
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{ __html: A11Y_PRE_HYDRATION_SCRIPT }}
        />
        <script
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{ __html: runtimeConfigScript }}
        />
      </head>
      <body className="bg-background font-sans text-foreground antialiased">
        <QueryProvider>
          <AuthProvider>
            <RunToastProvider>
              <AuthGate>
                <AppShell version={pkg.version}>{children}</AppShell>
              </AuthGate>
            </RunToastProvider>
          </AuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
