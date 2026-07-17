"use client";

// v2.11.0 — schedule an Azure auto-shutdown for one VM.
//
// The dashboard PATCHes Microsoft.DevTestLab/schedules — the same
// per-VM auto-shutdown Azure's own portal writes to. We only surface
// the fields analysts care about (enable, local time, timezone); the
// notification webhook stays off unless someone adds it later.
//
// Windows time-zone ids (Azure's native format) — not IANA. A small
// hand-curated list covers what analysts realistically need; expand as
// requested rather than pulling in a mapping table.

import { useEffect, useState } from "react";
import { CalendarClock, Loader2, Trash2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useAutoShutdown,
  useDeleteAutoShutdownMutation,
  usePutAutoShutdownMutation,
} from "@/lib/hooks";
import type { VmSummary } from "@/lib/types";

const WINDOWS_TIMEZONES: { id: string; label: string }[] = [
  { id: "UTC", label: "UTC" },
  { id: "Pacific Standard Time", label: "US Pacific" },
  { id: "Mountain Standard Time", label: "US Mountain" },
  { id: "Central Standard Time", label: "US Central" },
  { id: "Eastern Standard Time", label: "US Eastern" },
  { id: "GMT Standard Time", label: "UK / London" },
  { id: "Central European Standard Time", label: "Central Europe" },
  { id: "Israel Standard Time", label: "Israel" },
  { id: "India Standard Time", label: "India" },
  { id: "Singapore Standard Time", label: "Singapore" },
  { id: "Tokyo Standard Time", label: "Tokyo" },
  { id: "AUS Eastern Standard Time", label: "Sydney" },
];

// "1900" ↔ "19:00" — the wire format is 4-digit no-colon, but a browser
// <input type="time"> emits "HH:MM". Convert both directions.
function toInputTime(hhmm: string): string {
  const padded = hhmm.padStart(4, "0");
  return `${padded.slice(0, 2)}:${padded.slice(2, 4)}`;
}
function toWireTime(inputTime: string): string {
  return inputTime.replace(":", "").padStart(4, "0");
}

export function ScheduleModal({
  vm,
  open,
  onOpenChange,
}: {
  vm: VmSummary;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: existing, isLoading } = useAutoShutdown(vm.arm_id, {
    enabled: open,
  });
  const putM = usePutAutoShutdownMutation(vm.arm_id);
  const deleteM = useDeleteAutoShutdownMutation(vm.arm_id);

  const [time, setTime] = useState("19:00");
  const [tz, setTz] = useState("Central Standard Time");
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Seed from the persisted value each time the modal opens.
  useEffect(() => {
    if (!open) return;
    if (existing) {
      setTime(toInputTime(existing.time_hhmm));
      setTz(existing.timezone_id);
      setEnabled(existing.enabled);
    } else {
      setTime("19:00");
      setTz("Central Standard Time");
      setEnabled(true);
    }
    setError(null);
  }, [open, existing]);

  async function save() {
    setError(null);
    try {
      await putM.mutateAsync({
        enabled,
        time_hhmm: toWireTime(time),
        timezone_id: tz,
      });
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function removeSchedule() {
    setError(null);
    try {
      await deleteM.mutateAsync();
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const busy = putM.isPending || deleteM.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <CalendarClock className="h-4 w-4" />
            Auto-shutdown — {vm.name}
          </DialogTitle>
          <DialogDescription>
            Azure will deallocate this VM daily at the chosen local time.
            Data is preserved; compute charges stop. Uses the built-in
            <code className="mx-1 rounded bg-muted px-1 text-[10px]">
              Microsoft.DevTestLab/schedules
            </code>
            resource — the same feature the Azure portal writes to.
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            Loading current schedule…
          </div>
        ) : (
          <div className="space-y-4">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              Schedule enabled
            </label>

            <div>
              <label className="mb-1 block text-xs font-medium">
                Shutdown time (local to timezone)
              </label>
              <Input
                type="time"
                value={time}
                onChange={(e) => setTime(e.target.value)}
                disabled={!enabled}
                className="w-40"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium">Timezone</label>
              <select
                value={tz}
                onChange={(e) => setTz(e.target.value)}
                disabled={!enabled}
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
              >
                {WINDOWS_TIMEZONES.map((z) => (
                  <option key={z.id} value={z.id}>
                    {z.label} — {z.id}
                  </option>
                ))}
              </select>
            </div>

            {error && (
              <p className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
                {error}
              </p>
            )}

            <div className="flex items-center justify-between gap-2 pt-1">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!existing || busy}
                onClick={() => {
                  if (window.confirm(`Remove auto-shutdown schedule for ${vm.name}?`)) {
                    void removeSchedule();
                  }
                }}
                className="text-destructive"
                title={existing ? "Delete schedule" : "No schedule to delete"}
              >
                <Trash2 className="mr-1 h-3 w-3" />
                Remove
              </Button>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => onOpenChange(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={busy}
                  onClick={save}
                >
                  {busy && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                  {existing ? "Update" : "Create"}
                </Button>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
