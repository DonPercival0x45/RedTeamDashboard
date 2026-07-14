// v2.0.0 app shell — the flex row that owns the sidebar and the
// main content region. Rendered inside RootLayout so every page in
// the app inherits the same chrome.
//
// Look-and-feel: uses our existing theme tokens (bg-background,
// bg-card, border-border, text-foreground) exactly like the previous
// header did. Only the STRUCTURE (persistent sidebar, collapsed-only
// top bar) is new.

"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { PanelLeft } from "lucide-react";
import { LeftSidebar } from "@/components/app-shell/left-sidebar";
import { SettingsModal } from "@/components/settings/settings-modal";
import { WhatsNewBanner } from "@/components/whats-new-banner";
import { cn } from "@/lib/utils";

const COLLAPSED_STORAGE_KEY = "rtd.shell.sidebarCollapsed";

// Map the current pathname → a short breadcrumb label for the
// collapsed-only top bar. Fallback to the raw path so unknown routes
// still display something recognisable.
function breadcrumbForPath(pathname: string): string {
  if (pathname === "/" || pathname.startsWith("/engagements") || pathname.startsWith("/e") || pathname.startsWith("/new")) {
    return "Engagements";
  }
  if (pathname.startsWith("/automation")) return "Automation";
  if (pathname.startsWith("/analytics")) return "Analytics";
  if (pathname.startsWith("/infrastructure")) return "Infrastructure";
  if (pathname.startsWith("/settings")) return "Settings";
  return pathname;
}

export function AppShell({
  version,
  children,
}: {
  version: string;
  children: React.ReactNode;
}) {
  // Collapsed flag is persisted in localStorage so it survives reload.
  // We start `false` on the server to keep the SSR HTML stable, then
  // rehydrate on mount if the analyst had collapsed it previously.
  const [collapsed, setCollapsed] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const pathname = usePathname() ?? "/";

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem(COLLAPSED_STORAGE_KEY);
      if (raw === "1") setCollapsed(true);
    } catch {
      /* ignore quota / disabled storage */
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(COLLAPSED_STORAGE_KEY, collapsed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [collapsed]);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground">
      <LeftSidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((v) => !v)}
        onOpenSettings={() => setSettingsOpen(true)}
        version={version}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Post-release banner — sits above tab-scoped chrome so it's
            the first thing the analyst sees when a new version ships.
            Auto-hides once the version is dismissed (localStorage). */}
        <WhatsNewBanner />
        {collapsed && (
          <CollapsedTopBar
            pathname={pathname}
            version={version}
            onExpand={() => setCollapsed(false)}
          />
        )}
        <main className="flex-1 overflow-y-auto">
          <div className="container py-8">{children}</div>
        </main>
      </div>

      <SettingsModal
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
      />
    </div>
  );
}

// Rendered only when the sidebar is collapsed. Keeps the analyst
// oriented (breadcrumb) and puts the version + bell within reach so
// they don't lose global chrome to the collapsed sidebar.
function CollapsedTopBar({
  pathname,
  version,
  onExpand,
}: {
  pathname: string;
  version: string;
  onExpand: () => void;
}) {
  const label = breadcrumbForPath(pathname);
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-card px-4">
      <div className="flex min-w-0 items-center gap-3">
        <button
          type="button"
          onClick={onExpand}
          className="grid h-8 w-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          aria-label="Expand sidebar"
        >
          <PanelLeft className="h-4 w-4 rotate-180" />
        </button>
        <span className="text-xs text-muted-foreground">Project XR@Y</span>
        <span className="text-xs text-muted-foreground">/</span>
        <span className={cn("text-sm font-medium text-foreground")}>{label}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="rounded border border-border bg-background px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          v{version}
        </span>
      </div>
    </header>
  );
}
