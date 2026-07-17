"use client";

// v2.12.0 — Connect drawer. One-shot Run Command against the VM via
// Azure's ARM runCommand LRO. Analyst types a script (multi-line ok),
// hits Run, sees stdout + stderr side by side once the LRO returns.
//
// This is NOT an interactive terminal (see v2.13 for that). Each Run
// click is a standalone Azure operation, ~1-10s for lightweight
// commands, up to 90s before we time out. Prompt for prior runs is
// kept in a local history buffer so the analyst can revise without
// retyping.

import { useState } from "react";
import { Loader2, Play, Terminal } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useRunCommandMutation } from "@/lib/hooks";
import type { RunCommandResult, VmSummary } from "@/lib/types";

type HistoryEntry = {
  script: string;
  result: RunCommandResult;
  ts: number;
};

export function ConnectDrawer({
  vm,
  open,
  onOpenChange,
}: {
  vm: VmSummary;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [script, setScript] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const runM = useRunCommandMutation(vm.arm_id);

  const busy = runM.isPending;
  const os = vm.os_type;
  const promptHint =
    os.toLowerCase() === "windows" ? "PowerShell (RunPowerShellScript)" : "Bash (RunShellScript)";

  async function run() {
    const trimmed = script.trim();
    if (!trimmed) return;
    setError(null);
    try {
      const result = await runM.mutateAsync(trimmed);
      setHistory((prev) => [{ script: trimmed, result, ts: Date.now() }, ...prev]);
      setScript("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Terminal className="h-4 w-4" />
            Connect — {vm.name}
          </DialogTitle>
          <DialogDescription>
            Runs one command at a time via Azure Run Command ({promptHint}).
            Not an interactive shell — each Run is a standalone LRO. The VM
            must be running for the command to execute; failures are surfaced
            in the stderr pane.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium">Script</label>
            <Textarea
              value={script}
              onChange={(e) => setScript(e.target.value)}
              placeholder={
                os.toLowerCase() === "windows"
                  ? "Get-Process | Select-Object -First 5"
                  : "uname -a\nwhoami"
              }
              rows={5}
              className="font-mono text-xs"
              disabled={busy}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  void run();
                }
              }}
            />
            <p className="mt-1 text-[10px] text-muted-foreground">
              ⌘/Ctrl+Enter to run. Runs against {vm.os_type} on {vm.location}.
            </p>
          </div>

          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              {history.length > 0
                ? `${history.length} previous run${history.length === 1 ? "" : "s"} in this session`
                : "No runs yet"}
            </p>
            <Button
              type="button"
              size="sm"
              disabled={busy || !script.trim()}
              onClick={() => void run()}
            >
              {busy ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Play className="mr-1 h-3.5 w-3.5" />
              )}
              {busy ? "Running…" : "Run"}
            </Button>
          </div>

          {error && (
            <p className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
              {error}
            </p>
          )}

          {history.map((entry, i) => (
            <RunResult key={`${entry.ts}-${i}`} entry={entry} />
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RunResult({ entry }: { entry: HistoryEntry }) {
  const { script, result } = entry;
  const seconds = (result.duration_ms / 1000).toFixed(1);
  return (
    <div className="rounded-md border border-border bg-card/40 p-3">
      <details open>
        <summary className="flex cursor-pointer items-center gap-2 text-xs">
          <span className="font-mono text-muted-foreground">$</span>
          <span className="truncate font-mono text-foreground">
            {script.split("\n")[0]}
            {script.includes("\n") ? " ⋯" : ""}
          </span>
          <span className="ml-auto text-[10px] text-muted-foreground">
            {seconds}s
            {result.timed_out && (
              <span className="ml-1 rounded bg-amber-500/20 px-1 text-amber-800 dark:text-amber-300">
                timed out
              </span>
            )}
          </span>
        </summary>
        <div className="mt-2 space-y-2">
          {script.includes("\n") && (
            <pre className="max-h-24 overflow-auto rounded bg-background/60 p-2 font-mono text-[11px] text-muted-foreground">
              {script}
            </pre>
          )}
          {result.stdout && (
            <div>
              <div className="text-[10px] font-medium uppercase text-muted-foreground">
                stdout
              </div>
              <pre className="max-h-64 overflow-auto rounded bg-background/60 p-2 font-mono text-[11px]">
                {result.stdout}
              </pre>
            </div>
          )}
          {result.stderr && (
            <div>
              <div className="text-[10px] font-medium uppercase text-destructive">
                stderr
              </div>
              <pre className="max-h-64 overflow-auto rounded border border-destructive/30 bg-destructive/5 p-2 font-mono text-[11px] text-destructive">
                {result.stderr}
              </pre>
            </div>
          )}
          {!result.stdout && !result.stderr && (
            <p className="text-[10px] text-muted-foreground">
              (no output)
            </p>
          )}
        </div>
      </details>
    </div>
  );
}
