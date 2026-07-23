"use client";

// v3 Track A — kick a playbook run. Analyst picks scope items + executor
// (internal / mcp) and hits Kick. Backend returns 202 with the pending row
// (or awaiting_approval for active playbooks); the parent's usePlaybookRuns
// hook re-fetches immediately via the mutation's onSuccess invalidation.

import { useState } from "react";
import { Loader2 } from "lucide-react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useCreatePlaybookRunMutation } from "@/lib/hooks";
import type { PlaybookExecutorKind, PlaybookRead } from "@/lib/types";

export function KickRunModal({
  engagementSlug,
  playbook,
  onClose,
}: {
  engagementSlug: string;
  playbook: PlaybookRead;
  onClose: () => void;
}) {
  const [scopeText, setScopeText] = useState("");
  const [executor, setExecutor] = useState<PlaybookExecutorKind>("internal");
  const create = useCreatePlaybookRunMutation(engagementSlug);
  const [error, setError] = useState<string | null>(null);

  const scope = scopeText
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
  const canSubmit = scope.length > 0 && !create.isPending;

  const submit = async () => {
    setError(null);
    try {
      await create.mutateAsync({
        playbook_slug: playbook.slug,
        playbook_version: playbook.version,
        scope_subset: scope,
        executor,
      });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to kick run.");
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Kick playbook run</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <p className="text-sm font-medium">{playbook.name}</p>
            <p className="text-xs text-muted-foreground">
              v{playbook.version} · {playbook.step_count} steps ·{" "}
              {playbook.applies_to_asset_class}
              {playbook.active
                ? " · gated (analyst approval required)"
                : ""}
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="scope-subset">
              Scope selection ({scope.length})
            </Label>
            <Textarea
              id="scope-subset"
              value={scopeText}
              onChange={(e) => setScopeText(e.target.value)}
              placeholder={`Scope items (comma or newline separated).\nExample: foo.example, bar.example`}
              className="min-h-[6rem] font-mono text-xs"
            />
            <p className="text-xs text-muted-foreground">
              One scope-item identifier per line or separated by commas. The
              runner iterates each step against each scope item.
            </p>
          </div>

          <div className="space-y-2">
            <Label>Executor</Label>
            <div className="grid grid-cols-2 gap-2">
              {(["internal", "mcp"] as PlaybookExecutorKind[]).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  onClick={() => setExecutor(kind)}
                  className={`rounded-md border px-3 py-2 text-left text-xs transition-colors ${
                    executor === kind
                      ? "border-primary bg-primary/10"
                      : "border-border hover:bg-muted"
                  }`}
                >
                  <div className="font-medium uppercase text-[10px] tracking-wide">
                    {kind}
                  </div>
                  <div className="mt-0.5 text-muted-foreground text-[11px]">
                    {kind === "internal"
                      ? "In-process (default)"
                      : "MCP server"}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {error ? (
            <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>
          ) : null}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" size="sm" disabled={create.isPending}>
              Cancel
            </Button>
          </DialogClose>
          <Button size="sm" onClick={submit} disabled={!canSubmit}>
            {create.isPending ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : null}
            Kick run
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
