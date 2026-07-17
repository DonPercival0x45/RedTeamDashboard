"use client";

// v2.10.0 — three tiles across the top of the Infrastructure page:
// Running VMs · Stopped · Active Agents. Layout mirrors the mockup
// (Webpage Layout Redesign); colors resolve to our theme tokens rather
// than the reference palette. "Active Agents" surfaces "—" until a
// later phase wires the agent registry into this view.

import { cn } from "@/lib/utils";
import type { VmSummary } from "@/lib/types";

export function InfraStatTiles({
  vms,
  loading,
  onFilter,
  activeFilter,
}: {
  vms: VmSummary[];
  loading: boolean;
  onFilter: (filter: "running" | "stopped" | null) => void;
  activeFilter: "running" | "stopped" | null;
}) {
  const running = vms.filter((v) => v.power_state === "running").length;
  const stopped = vms.filter(
    (v) =>
      v.power_state === "stopped" ||
      v.power_state === "deallocated" ||
      v.power_state === "deallocating",
  ).length;

  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <StatTile
        label="Running VMs"
        value={running}
        loading={loading}
        active={activeFilter === "running"}
        accent="text-emerald-600 dark:text-emerald-400"
        onClick={() =>
          onFilter(activeFilter === "running" ? null : "running")
        }
      />
      <StatTile
        label="Stopped"
        value={stopped}
        loading={loading}
        active={activeFilter === "stopped"}
        accent="text-muted-foreground"
        onClick={() =>
          onFilter(activeFilter === "stopped" ? null : "stopped")
        }
      />
      <StatTile
        label="Active Agents"
        value="—"
        loading={false}
        accent="text-muted-foreground"
        hint="Coming in a later phase"
        disabled
      />
    </div>
  );
}

function StatTile({
  label,
  value,
  loading,
  accent,
  onClick,
  active = false,
  hint,
  disabled = false,
}: {
  label: string;
  value: number | string;
  loading: boolean;
  accent: string;
  onClick?: () => void;
  active?: boolean;
  hint?: string;
  disabled?: boolean;
}) {
  const clickable = !!onClick && !disabled;
  return (
    <button
      type="button"
      onClick={clickable ? onClick : undefined}
      disabled={disabled}
      title={hint}
      className={cn(
        "flex flex-col rounded-lg border p-4 text-left transition-colors",
        active
          ? "border-critical bg-critical/5"
          : "border-border bg-card/40 hover:bg-card/60",
        clickable ? "cursor-pointer" : "cursor-default",
        disabled && "opacity-70",
      )}
      aria-pressed={active}
    >
      <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className={cn("mt-2 text-3xl font-semibold tabular-nums", accent)}>
        {loading ? "…" : value}
      </span>
      {hint && (
        <span className="mt-1 text-[10px] text-muted-foreground">{hint}</span>
      )}
    </button>
  );
}
