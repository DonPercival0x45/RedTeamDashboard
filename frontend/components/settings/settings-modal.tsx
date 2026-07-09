"use client";

// v1.25.0 — Settings modal.
//
// Replaces the icon-strip in the header with a single gear-triggered
// modal. Sidebar order + labels per operator request:
//   Models · Configurations · Appearance · Feedback · Integrations ·
//   Tools · Account Management · Accessibility
// (What's New moved to a version pill next to the analyst name.)
//
// Panel bodies are the existing `/settings/*` page components imported
// wholesale — the routed pages still work as deep links, but the modal
// is the primary entry point. Admin-only panels are hidden for
// non-admins (Integrations / Tools / Account Management).
import dynamic from "next/dynamic";
import { useMemo, useState, type ComponentType } from "react";
import {
  Accessibility,
  Globe,
  Key,
  MessageSquare,
  Palette,
  SlidersHorizontal,
  UserCog,
  Wrench,
  X,
} from "lucide-react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useMe } from "@/lib/hooks";
import { cn } from "@/lib/utils";

// Lazy-import each panel body. next/dynamic with ssr:false is fine —
// the modal is client-only. Loading fallback is a lightweight
// centered spinner via the shared skeleton pattern.
const ModelsPanel = dynamic(
  () => import("@/app/settings/keys/page").then((m) => m.default),
  { ssr: false, loading: () => <PanelSkeleton /> },
);
const ConfigurationsPanel = dynamic(
  () => import("@/app/settings/configurations/page").then((m) => m.default),
  { ssr: false, loading: () => <PanelSkeleton /> },
);
const AppearancePanel = dynamic(
  () => import("@/app/settings/appearance/page").then((m) => m.default),
  { ssr: false, loading: () => <PanelSkeleton /> },
);
const FeedbackPanel = dynamic(
  () => import("@/app/settings/feedback/page").then((m) => m.default),
  { ssr: false, loading: () => <PanelSkeleton /> },
);
const IntegrationsPanel = dynamic(
  () => import("@/app/settings/integrations/page").then((m) => m.default),
  { ssr: false, loading: () => <PanelSkeleton /> },
);
const ToolsPanel = dynamic(
  () => import("@/app/settings/tools/page").then((m) => m.default),
  { ssr: false, loading: () => <PanelSkeleton /> },
);
const ManagementPanel = dynamic(
  () => import("@/app/settings/management/page").then((m) => m.default),
  { ssr: false, loading: () => <PanelSkeleton /> },
);
const AccessibilityRoutedPanel = dynamic(
  () => import("@/app/settings/accessibility/page").then((m) => m.default),
  { ssr: false, loading: () => <PanelSkeleton /> },
);

function PanelSkeleton() {
  return (
    <div className="grid animate-pulse gap-3">
      <div className="h-5 w-40 rounded bg-muted" />
      <div className="h-3 w-64 rounded bg-muted" />
      <div className="mt-4 h-24 rounded bg-muted" />
    </div>
  );
}

type PanelKey =
  | "models"
  | "configurations"
  | "appearance"
  | "feedback"
  | "integrations"
  | "tools"
  | "management"
  | "accessibility";

interface PanelEntry {
  key: PanelKey;
  label: string;
  icon: ComponentType<{ className?: string }>;
  Component: ComponentType;
  adminOnly?: boolean;
}

const PANELS: PanelEntry[] = [
  { key: "models", label: "Models", icon: Key, Component: ModelsPanel },
  {
    key: "configurations",
    label: "Configurations",
    icon: SlidersHorizontal,
    Component: ConfigurationsPanel,
  },
  {
    key: "appearance",
    label: "Appearance",
    icon: Palette,
    Component: AppearancePanel,
  },
  {
    key: "feedback",
    label: "Feedback",
    icon: MessageSquare,
    Component: FeedbackPanel,
  },
  {
    key: "integrations",
    label: "Integrations",
    icon: Globe,
    Component: IntegrationsPanel,
    adminOnly: true,
  },
  { key: "tools", label: "Tools", icon: Wrench, Component: ToolsPanel, adminOnly: true },
  {
    key: "management",
    label: "Account Management",
    icon: UserCog,
    Component: ManagementPanel,
    adminOnly: true,
  },
  {
    key: "accessibility",
    label: "Accessibility",
    icon: Accessibility,
    Component: AccessibilityRoutedPanel,
  },
];

export function SettingsModal({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const { data: me } = useMe();
  const isAdmin = Boolean(me?.is_admin);
  const visible = useMemo(
    () => PANELS.filter((p) => !p.adminOnly || isAdmin),
    [isAdmin],
  );
  const [active, setActive] = useState<PanelKey>("models");

  const activePanel = visible.find((p) => p.key === active) ?? visible[0];
  const Body = activePanel?.Component;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          aria-label="Settings"
          className={cn(
            "fixed left-[50%] top-[50%] z-50 flex h-[85vh] w-[95vw] max-w-5xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-lg border border-border bg-background shadow-2xl",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
          )}
        >
          <header className="flex items-center justify-between border-b border-border px-5 py-3">
            <h2 className="text-base font-semibold">Settings</h2>
            <DialogPrimitive.Close
              aria-label="Close settings"
              className="rounded-sm p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </DialogPrimitive.Close>
          </header>
          <div className="flex min-h-0 flex-1">
            <aside className="w-56 shrink-0 border-r border-border p-2">
              <nav className="grid gap-0.5">
                {visible.map((p) => {
                  const isActive = p.key === active;
                  const Icon = p.icon;
                  return (
                    <button
                      key={p.key}
                      type="button"
                      onClick={() => setActive(p.key)}
                      aria-current={isActive ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-2 rounded px-2.5 py-1.5 text-left text-sm transition-colors",
                        isActive
                          ? "bg-primary/10 text-foreground"
                          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      <span className="truncate">{p.label}</span>
                    </button>
                  );
                })}
              </nav>
            </aside>
            <section
              className="min-h-0 flex-1 overflow-auto p-4"
              data-in-settings-modal="true"
            >
              {Body && <Body />}
            </section>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
