import type { Metadata } from "next";
import Link from "next/link";
import { AuthGate } from "@/components/auth-gate";
import { IdentityMenu } from "@/components/identity-menu";
import { WhatsNewBanner } from "@/components/whats-new-banner";
import { QueryProvider } from "@/components/query-provider";
import { RunToastProvider } from "@/components/run-toast-provider";
import { AuthProvider } from "@/lib/auth";
import { readServerConfig, RUNTIME_CONFIG_WINDOW_KEY } from "@/lib/config";
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

  // `dark` is pinned on <html>: the app is always the monochrome dark theme.
  return (
    <html lang="en" className="dark">
      <head>
        <script
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{ __html: runtimeConfigScript }}
        />
      </head>
      <body className="min-h-screen bg-background font-sans text-foreground antialiased">
        <QueryProvider>
          <AuthProvider>
            <RunToastProvider>
              <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur">
                <div className="container flex h-14 items-center justify-between">
                  <Link href="/" className="group flex items-center gap-2.5">
                    {/* The lone accent in the chrome — a single ember mark. */}
                    <span className="h-3.5 w-1 rounded-full bg-critical" />
                    <span className="text-sm font-semibold tracking-tight">
                      Project XR@Y
                    </span>
                  </Link>
                  <IdentityMenu />
                </div>
              </header>
              <WhatsNewBanner />
              <AuthGate>
                <main className="container py-8">{children}</main>
              </AuthGate>
            </RunToastProvider>
          </AuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
