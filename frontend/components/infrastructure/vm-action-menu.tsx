"use client";

// v2.10.0 — per-row action group. Renders Start/Stop/Restart based on
// the VM's power state, plus disabled LiveConnect + Schedule buttons
// with hover tooltips explaining they land in v2.11 / v2.12. Destructive
// actions confirm via `window.confirm` (matches status-view pattern).

import { useState } from "react";
import { CalendarClock, Play, RotateCcw, Square, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  useDeallocateVmMutation,
  useRestartVmMutation,
  useStartVmMutation,
} from "@/lib/hooks";
import type { VmSummary } from "@/lib/types";

export function VmActionMenu({ vm }: { vm: VmSummary }) {
  const [busy, setBusy] = useState<null | "start" | "stop" | "restart">(null);
  const startM = useStartVmMutation();
  const stopM = useDeallocateVmMutation();
  const restartM = useRestartVmMutation();

  const inFlight = vm.power_state === "starting" ||
    vm.power_state === "stopping" ||
    vm.power_state === "deallocating";
  const canStart = vm.power_state === "stopped" || vm.power_state === "deallocated";
  const canStop = vm.power_state === "running";
  const canRestart = vm.power_state === "running";

  async function run(
    kind: "start" | "stop" | "restart",
    mutation: { mutateAsync: (arm: string) => Promise<unknown> },
  ) {
    setBusy(kind);
    try {
      await mutation.mutateAsync(vm.arm_id);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!canStart || busy !== null || inFlight}
        onClick={() => run("start", startM)}
        title="Start VM"
      >
        <Play className="mr-1 h-3.5 w-3.5" />
        Start
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!canStop || busy !== null || inFlight}
        onClick={() => {
          if (window.confirm(`Deallocate ${vm.name}? Compute charges stop; data preserved.`)) {
            void run("stop", stopM);
          }
        }}
        title="Deallocate VM (stops compute charges)"
      >
        <Square className="mr-1 h-3.5 w-3.5" />
        Stop
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!canRestart || busy !== null || inFlight}
        onClick={() => {
          if (window.confirm(`Restart ${vm.name}?`)) {
            void run("restart", restartM);
          }
        }}
        title="Restart VM"
      >
        <RotateCcw className="mr-1 h-3.5 w-3.5" />
        Restart
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled
        title="Coming in v2.11 — Azure auto-shutdown schedule"
      >
        <CalendarClock className="mr-1 h-3.5 w-3.5" />
        Schedule
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled
        title="Coming in v2.12 — serial console web terminal"
      >
        <Terminal className="mr-1 h-3.5 w-3.5" />
        LiveConnect
      </Button>
    </div>
  );
}
