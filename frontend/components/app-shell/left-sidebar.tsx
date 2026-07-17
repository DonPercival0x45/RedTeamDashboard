// v2.0.0 app shell — persistent left sidebar. Replaces the old
// sticky top header (Project XR@Y + IdentityMenu) with the four
// top-level nav sections (Engagements / Automation / Analytics /
// Infrastructure), a Settings row that opens the existing modal,
// and a user chip at the bottom.
//
// Design source: docs zip → design_handoff_xray_dashboard. We keep
// the STRUCTURE (widths, transitions, collapsed-topbar pattern) but
// the LOOK inherits our existing theme tokens (bg-card, border-border,
// text-foreground/muted-foreground, ember-red accent) rather than
// the reference's violet gradient / large radius.

"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  Bell,
  Crosshair,
  HardDrive,
  LogOut,
  PanelLeft,
  Settings,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { useEngagements, useMe } from "@/lib/hooks";
import { WhatsNewModal } from "@/components/settings/whats-new-modal";
import { ApprovalInbox } from "@/components/approval-inbox";

// Nav item shape — icon (lucide), label, href, and an optional live
// badge count fetched by the sidebar (currently only Engagements).
// v2.10.0: `adminOnly` hides the row for non-admin roles so the
// Infrastructure surface — which controls tenant VMs — never appears
// in the nav for users/guests. Direct URL still hits the AdminOnlyGate.
type NavItem = {
  id: "engagements" | "automation" | "analytics" | "infrastructure";
  label: string;
  href: string;
  icon: typeof Crosshair;
  adminOnly?: boolean;
};

const NAV_ITEMS: NavItem[] = [
  { id: "engagements", label: "Engagements", href: "/engagements", icon: Crosshair },
  { id: "automation", label: "Automation", href: "/automation", icon: Zap },
  { id: "analytics", label: "Analytics", href: "/analytics", icon: BarChart3 },
  { id: "infrastructure", label: "Infrastructure", href: "/infrastructure", icon: HardDrive, adminOnly: true },
];

// Path prefix → active nav id. `/e/*` and `/new` count as Engagements
// sub-routes so the highlight stays consistent when the analyst drills
// in to a finding or spawns a new engagement.
function activeNavId(pathname: string): NavItem["id"] | null {
  if (
    pathname === "/" ||
    pathname === "/engagements" ||
    pathname.startsWith("/engagements/") ||
    pathname.startsWith("/e") ||
    pathname.startsWith("/new")
  ) {
    return "engagements";
  }
  if (pathname.startsWith("/automation")) return "automation";
  if (pathname.startsWith("/analytics")) return "analytics";
  if (pathname.startsWith("/infrastructure")) return "infrastructure";
  return null;
}

export function LeftSidebar({
  collapsed,
  onToggle,
  onOpenSettings,
  version,
}: {
  collapsed: boolean;
  onToggle: () => void;
  onOpenSettings: () => void;
  version: string;
}) {
  const pathname = usePathname() ?? "/";
  const active = activeNavId(pathname);
  const { data: engagements } = useEngagements();
  const { data: me } = useMe();
  const visibleNavItems = NAV_ITEMS.filter(
    (item) => !item.adminOnly || me?.is_admin,
  );

  // Only Engagements gets a badge in v2.0.0 — the other nav items
  // don't have live counts to report until their features ship.
  const engagementsCount = engagements?.length ?? null;

  const width = collapsed ? "w-[78px]" : "w-[252px]";

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-border bg-card transition-[width] duration-200 ease-out",
        width,
      )}
      aria-label="Primary"
    >
      {/* Brand row */}
      <div
        className={cn(
          "flex items-center gap-3 border-b border-border/60",
          collapsed ? "justify-center px-4 py-4" : "justify-between px-4 py-4",
        )}
      >
        <Link
          href="/engagements"
          className={cn(
            "flex items-center gap-2.5",
            collapsed && "justify-center",
          )}
          aria-label="Project XR@Y — Engagements"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-background">
            <Crosshair className="h-4 w-4 text-critical" />
          </span>
          {!collapsed && (
            <span className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-sm font-semibold tracking-tight">
                Project XR@Y
              </span>
              <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                Security Workspace
              </span>
            </span>
          )}
        </Link>
        {!collapsed && (
          <button
            type="button"
            onClick={onToggle}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label="Collapse sidebar"
          >
            <PanelLeft className="h-4 w-4" />
          </button>
        )}
      </div>

      {collapsed && (
        <button
          type="button"
          onClick={onToggle}
          className="mx-auto mt-2 grid h-7 w-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          aria-label="Expand sidebar"
        >
          <PanelLeft className="h-4 w-4 rotate-180" />
        </button>
      )}

      {/* Section label — expanded only */}
      {!collapsed && (
        <div className="px-4 pb-2 pt-3 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
          Navigate
        </div>
      )}

      {/* Nav */}
      <nav className="flex flex-col gap-1 px-2">
        {visibleNavItems.map((item) => {
          const isActive = active === item.id;
          const Icon = item.icon;
          const badge = item.id === "engagements" ? engagementsCount : null;
          return (
            <Link
              key={item.id}
              href={item.href}
              className={cn(
                "group relative flex items-center gap-3 rounded-md text-sm font-medium transition-colors",
                collapsed ? "justify-center px-0 py-2.5" : "px-3 py-2.5",
                isActive
                  ? "bg-critical/10 text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
              aria-current={isActive ? "page" : undefined}
              title={collapsed ? item.label : undefined}
            >
              {/* Active accent bar — left edge, ember-red */}
              {isActive && (
                <span
                  aria-hidden
                  className="absolute inset-y-2 left-0 w-[3px] rounded-r bg-critical"
                />
              )}
              <Icon
                className={cn(
                  "h-4 w-4 shrink-0",
                  isActive ? "text-critical" : "text-muted-foreground group-hover:text-foreground",
                )}
              />
              {!collapsed && (
                <>
                  <span className="flex-1 truncate">{item.label}</span>
                  {typeof badge === "number" && badge > 0 && (
                    <span
                      className={cn(
                        "shrink-0 rounded-full px-2 py-0.5 font-mono text-[10px] font-semibold",
                        isActive
                          ? "bg-critical/20 text-critical"
                          : "bg-muted text-muted-foreground",
                      )}
                    >
                      {badge}
                    </span>
                  )}
                </>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Footer chrome — Notifications + Settings + user chip */}
      <div className={cn("flex flex-col gap-1 border-t border-border p-2")}>
        <ApprovalInbox variant="sidebar" collapsed={collapsed} />
        <button
          type="button"
          onClick={onOpenSettings}
          className={cn(
            "flex w-full items-center gap-3 rounded-md text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
            collapsed ? "justify-center px-0 py-2" : "px-3 py-2",
          )}
          title={collapsed ? "Settings" : undefined}
        >
          <Settings className="h-4 w-4 shrink-0" />
          {!collapsed && <span>Settings</span>}
        </button>
      </div>

      <UserChip collapsed={collapsed} version={version} />
    </aside>
  );
}

// Bottom user chip — identity name, version pill, What's New, sign out.
// Mirrors the old IdentityMenu but in a vertical layout that fits the
// sidebar's narrow width.
function UserChip({
  collapsed,
  version,
}: {
  collapsed: boolean;
  version: string;
}) {
  const { identity, enabled, signOut } = useAuth();
  const [whatsNewOpen, setWhatsNewOpen] = useState(false);

  const displayName = identity?.name ?? "Signed out";
  const initials = displayName
    .split(/[\s._-]+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase() || "NC";

  return (
    <>
      <div
        className={cn(
          "flex items-center gap-3 border-t border-border px-3 py-3",
          collapsed && "justify-center px-0",
        )}
      >
        <span
          className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-critical/15 font-mono text-[11px] font-semibold text-critical"
          title={displayName}
        >
          {initials}
        </span>
        {!collapsed && (
          <>
            <span className="flex min-w-0 flex-1 flex-col leading-tight">
              <span className="truncate text-xs font-medium" title={displayName}>
                {displayName}
                {!enabled && (
                  <span className="ml-1 text-[10px] text-muted-foreground">(dev)</span>
                )}
              </span>
              <button
                type="button"
                onClick={() => setWhatsNewOpen(true)}
                className="mt-0.5 flex items-center gap-1 font-mono text-[10px] text-muted-foreground hover:text-foreground"
                aria-label={`Version v${version}. What's new?`}
              >
                <span>v{version}</span>
                <Bell className="h-2.5 w-2.5" />
              </button>
            </span>
            {enabled && (
              <button
                type="button"
                onClick={signOut}
                className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                aria-label="Sign out"
              >
                <LogOut className="h-4 w-4" />
              </button>
            )}
          </>
        )}
      </div>
      <WhatsNewModal open={whatsNewOpen} onOpenChange={setWhatsNewOpen} />
    </>
  );
}
