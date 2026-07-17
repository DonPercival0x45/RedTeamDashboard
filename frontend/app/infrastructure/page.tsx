"use client";

// v2.10.0 — Infrastructure tab. Admin-only surface listing every VM
// across every configured Azure subscription with power actions
// (Start / Stop / Restart). Schedule + LiveConnect land in v2.11 and
// v2.12; their buttons render but are disabled with tooltips today.

import { useState } from "react";
import { AdminOnlyGate } from "@/components/admin-only-gate";
import { InfraStatTiles } from "@/components/infrastructure/infra-stat-tiles";
import { VmTable } from "@/components/infrastructure/vm-table";
import { useInfraStatus, useMe, useVms } from "@/lib/hooks";

export default function InfrastructurePage() {
  return (
    <AdminOnlyGate>
      <InfrastructureView />
    </AdminOnlyGate>
  );
}

function InfrastructureView() {
  const { data: me } = useMe();
  const enabled = !!me?.is_admin;
  const statusQuery = useInfraStatus({ enabled });
  const vmsQuery = useVms({ enabled });
  const [filter, setFilter] = useState<"running" | "stopped" | null>(null);

  const vms = vmsQuery.data ?? [];
  const mockBanner = statusQuery.data?.mock ? (
    <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
      Mock mode — showing fixture VMs. Configure <code>RTD_INFRA_SUBSCRIPTIONS</code>
      on the backend (via <code>install.sh --infra-subscriptions</code>) to see
      real tenant VMs.
    </div>
  ) : null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Infrastructure</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage compute across every configured Azure subscription — inventory,
          power actions, scheduled shutdown, live connect.
        </p>
      </div>

      {mockBanner}

      <InfraStatTiles
        vms={vms}
        loading={vmsQuery.isLoading}
        onFilter={setFilter}
        activeFilter={filter}
      />

      <VmTable vms={vms} loading={vmsQuery.isLoading} filter={filter} />
    </div>
  );
}
