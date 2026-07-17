"use client";

// v2.10.0 — per-row action group. Renders Start/Stop/Restart based on
// the VM's power state, plus disabled Connect + Schedule buttons with
// hover tooltips explaining they land in v2.11 / v2.12. Destructive
// actions confirm via `window.confirm` (matches status-view pattern).
//
// v2.10.1: compact chip styling (h-6, text-[11px], tight gap) so all
// five actions fit one line in the table without horizontal scroll.
// The `sm` Button variant is 36px tall — too much when the row itself
// is ~52px tall. Override className to override cva height + padding.
// Also renamed LiveConnect → Connect.

import { useState } from "react";
import { CalendarClock, Play, RotateCcw, Square, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConnectDrawer } from "@/components/infrastructure/connect-drawer";
import { ScheduleModal } from "@/components/infrastructure/schedule-modal";
import {
  useDeallocateVmMutation,
  useRestartVmMutation,
  useStartVmMutation,
} from "@/lib/hooks";
import type { VmSummary } from "@/lib/types";

// Shared chip sizing — small enough that Start/Stop/Restart/Schedule/
// LiveConnect fit on one line at the default shell width.
const CHIP = "h-6 gap-1 px-2 text-[11px] font-medium";

export function VmActionMenu({ vm }: { vm: VmSummary }) {
  const [busy, setBusy] = useState<null | "start" | "stop" | "restart">(null);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
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
    <div className="flex flex-nowrap items-center justify-end gap-1">
      <Button
        type="button"
        size="sm"
        variant="outline"
        className={CHIP}
        disabled={!canStart || busy !== null || inFlight}
        onClick={() => run("start", startM)}
        title="Start VM"
      >
        <Play className="h-3 w-3" />
        Start
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className={CHIP}
        disabled={!canStop || busy !== null || inFlight}
        onClick={() => {
          if (window.confirm(`Deallocate ${vm.name}? Compute charges stop; data preserved.`)) {
            void run("stop", stopM);
          }
        }}
        title="Deallocate VM (stops compute charges)"
      >
        <Square className="h-3 w-3" />
        Stop
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className={CHIP}
        disabled={!canRestart || busy !== null || inFlight}
        onClick={() => {
          if (window.confirm(`Restart ${vm.name}?`)) {
            void run("restart", restartM);
          }
        }}
        title="Restart VM"
      >
        <RotateCcw className="h-3 w-3" />
        Restart
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className={CHIP}
        onClick={() => setScheduleOpen(true)}
        title="Auto-shutdown schedule (Azure DevTest Labs)"
      >
        <CalendarClock className="h-3 w-3" />
        Schedule
      </Button>
      <ScheduleModal vm={vm} open={scheduleOpen} onOpenChange={setScheduleOpen} />
      <Button
        type="button"
        size="sm"
        variant="outline"
        className={CHIP}
        onClick={() => setConnectOpen(true)}
        disabled={vm.power_state !== "running"}
        title={
          vm.power_state === "running"
            ? "Run one command at a time via Azure Run Command"
            : "VM must be running to Connect"
        }
      >
        <Terminal className="h-3 w-3" />
        Connect
      </Button>
      <ConnectDrawer vm={vm} open={connectOpen} onOpenChange={setConnectOpen} />
    </div>
  );
}
