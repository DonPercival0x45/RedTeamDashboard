"use client";

// v2.10.0 — VM inventory table. Columns:
//   Name · Region · OS · Public IP · Status · Actions
// Sortable by Name / Region / Status. Filter by search (name/rg/tags)
// and by the parent page's stat-tile filter (running/stopped).

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { VmActionMenu } from "@/components/infrastructure/vm-action-menu";
import { cn } from "@/lib/utils";
import type { VmPowerState, VmSummary } from "@/lib/types";

type SortKey = "name" | "region" | "status";

const POWER_LABEL: Record<VmPowerState, string> = {
  running: "Running",
  stopped: "Stopped",
  deallocated: "Deallocated",
  starting: "Starting",
  stopping: "Stopping",
  deallocating: "Deallocating",
  unknown: "Unknown",
};

const POWER_BADGE: Record<VmPowerState, string> = {
  running: "border-emerald-500/40 text-emerald-700 dark:text-emerald-300",
  stopped: "border-zinc-500/40 text-muted-foreground",
  deallocated: "border-zinc-500/40 text-muted-foreground",
  starting: "border-amber-500/40 text-amber-700 dark:text-amber-300",
  stopping: "border-amber-500/40 text-amber-700 dark:text-amber-300",
  deallocating: "border-amber-500/40 text-amber-700 dark:text-amber-300",
  unknown: "border-zinc-500/40 text-muted-foreground",
};

function powerBucket(p: VmPowerState): "running" | "stopped" | "other" {
  if (p === "running") return "running";
  if (p === "stopped" || p === "deallocated" || p === "deallocating") return "stopped";
  return "other";
}

export function VmTable({
  vms,
  loading,
  filter,
}: {
  vms: VmSummary[];
  loading: boolean;
  filter: "running" | "stopped" | null;
}) {
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    let rows = vms;
    if (filter) {
      rows = rows.filter((v) => powerBucket(v.power_state) === filter);
    }
    if (q) {
      rows = rows.filter((v) =>
        [
          v.name,
          v.resource_group,
          v.location,
          v.os_type,
          v.os_offer ?? "",
          v.public_ip ?? "",
          v.private_ip ?? "",
          ...Object.entries(v.tags).map(([k, val]) => `${k}=${val}`),
        ]
          .join(" ")
          .toLowerCase()
          .includes(q),
      );
    }
    const dir = sortDir === "asc" ? 1 : -1;
    const sorted = [...rows].sort((a, b) => {
      if (sortKey === "name") return a.name.localeCompare(b.name) * dir;
      if (sortKey === "region") return a.location.localeCompare(b.location) * dir;
      return a.power_state.localeCompare(b.power_state) * dir;
    });
    return sorted;
  }, [vms, filter, search, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card/40">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 p-3">
        <div>
          <h2 className="text-sm font-medium">Virtual machines</h2>
          <p className="text-xs text-muted-foreground">
            {loading
              ? "Fetching from Azure…"
              : `${visible.length} of ${vms.length} shown`}
          </p>
        </div>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name, RG, tag, IP…"
            aria-label="Search VMs"
            className="w-72 pl-8"
          />
        </div>
      </header>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <SortableTh
                label="Name"
                sortKey="name"
                current={sortKey}
                dir={sortDir}
                onClick={() => toggleSort("name")}
              />
              <SortableTh
                label="Region"
                sortKey="region"
                current={sortKey}
                dir={sortDir}
                onClick={() => toggleSort("region")}
              />
              <th className="px-3 py-2 font-medium">OS</th>
              <th className="px-3 py-2 font-medium">Public IP</th>
              <SortableTh
                label="Status"
                sortKey="status"
                current={sortKey}
                dir={sortDir}
                onClick={() => toggleSort("status")}
              />
              <th className="px-3 py-2 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {loading && vms.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-muted-foreground">
                  Loading VMs…
                </td>
              </tr>
            )}
            {!loading && vms.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-muted-foreground">
                  No VMs surfaced. Check that the backend has the right
                  subscription IDs configured.
                </td>
              </tr>
            )}
            {visible.map((vm) => (
              <tr key={vm.arm_id} className="hover:bg-muted/30">
                <td className="px-3 py-2">
                  <div className="font-medium text-foreground">{vm.name}</div>
                  <div className="text-[10px] text-muted-foreground">
                    {vm.resource_group} · {vm.size}
                  </div>
                </td>
                <td className="px-3 py-2 text-xs">{vm.location}</td>
                <td className="px-3 py-2 text-xs">
                  <div>{vm.os_type}</div>
                  {vm.os_offer && (
                    <div className="text-[10px] text-muted-foreground">
                      {vm.os_offer}
                    </div>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-xs">
                  {vm.public_ip ?? <span className="text-muted-foreground">—</span>}
                </td>
                <td className="px-3 py-2">
                  <Badge variant="outline" className={cn(POWER_BADGE[vm.power_state])}>
                    {POWER_LABEL[vm.power_state]}
                  </Badge>
                </td>
                <td className="px-3 py-2 text-right">
                  <VmActionMenu vm={vm} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SortableTh({
  label,
  sortKey,
  current,
  dir,
  onClick,
}: {
  label: string;
  sortKey: SortKey;
  current: SortKey;
  dir: "asc" | "desc";
  onClick: () => void;
}) {
  const active = current === sortKey;
  return (
    <th className="px-3 py-2">
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "flex items-center gap-1 font-medium hover:text-foreground",
          active && "text-foreground",
        )}
      >
        {label}
        {active && (dir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
      </button>
    </th>
  );
}
