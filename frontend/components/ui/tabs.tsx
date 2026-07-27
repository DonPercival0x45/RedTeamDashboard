"use client";

// Lightweight, dependency-free Tabs primitive styled to match the existing
// monochrome UI (underline tabs). Avoids pulling in @radix-ui/react-tabs;
// can be swapped for the shadcn/Radix Tabs later without touching call sites.
//
// Uncontrolled by default (pass `defaultValue`); pass `value` + `onValueChange`
// for controlled usage. Inactive panels are unmounted (state they need to
// survive a tab switch should live in a parent, as it does on StrategyView).

import * as React from "react";

import { cn } from "@/lib/utils";

interface TabsContextValue {
  value: string;
  setValue: (value: string) => void;
  baseId: string;
}

const TabsContext = React.createContext<TabsContextValue | null>(null);

export function Tabs({
  value,
  defaultValue,
  onValueChange,
  className,
  children,
}: {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  className?: string;
  children: React.ReactNode;
}) {
  const [internal, setInternal] = React.useState(defaultValue ?? "");
  const current = value ?? internal;
  const baseId = React.useId().replaceAll(":", "");
  const setValue = React.useCallback(
    (next: string) => {
      if (value === undefined) setInternal(next);
      onValueChange?.(next);
    },
    [value, onValueChange],
  );
  return (
    <TabsContext.Provider value={{ value: current, setValue, baseId }}>
      <div className={className}>{children}</div>
    </TabsContext.Provider>
  );
}

export function TabsList({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      role="tablist"
      className={cn(
        "flex flex-wrap items-center gap-1 border-b border-border",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function TabsTrigger({
  value,
  className,
  children,
}: {
  value: string;
  className?: string;
  children: React.ReactNode;
}) {
  const ctx = React.useContext(TabsContext);
  if (!ctx) throw new Error("TabsTrigger must be used within <Tabs>");
  const active = ctx.value === value;
  const tabId = `${ctx.baseId}-tab-${value}`;
  const panelId = `${ctx.baseId}-panel-${value}`;
  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = Array.from(
      event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
        '[role="tab"]:not(:disabled)',
      ) ?? [],
    );
    if (tabs.length === 0) return;
    const currentIndex = tabs.indexOf(event.currentTarget);
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabs.length - 1;
    event.preventDefault();
    tabs[nextIndex]?.focus();
    tabs[nextIndex]?.click();
  };
  return (
    <button
      id={tabId}
      type="button"
      role="tab"
      aria-selected={active}
      aria-controls={panelId}
      tabIndex={active ? 0 : -1}
      onKeyDown={handleKeyDown}
      onClick={() => ctx.setValue(value)}
      className={cn(
        "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-foreground text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function TabsContent({
  value,
  className,
  children,
}: {
  value: string;
  className?: string;
  children: React.ReactNode;
}) {
  const ctx = React.useContext(TabsContext);
  if (!ctx) throw new Error("TabsContent must be used within <Tabs>");
  if (ctx.value !== value) return null;
  return (
    <div
      id={`${ctx.baseId}-panel-${value}`}
      role="tabpanel"
      aria-labelledby={`${ctx.baseId}-tab-${value}`}
      tabIndex={0}
      className={className}
    >
      {children}
    </div>
  );
}
