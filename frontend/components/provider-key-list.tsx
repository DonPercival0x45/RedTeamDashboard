"use client";

import { useState } from "react";
import { Check, ListChecks, Loader2, Trash2, Wifi } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SelectModelsModal } from "@/components/settings/select-models-modal";
import { deleteProviderKey, probeSavedProviderKey } from "@/lib/api";
import type { ProviderKey, ProviderKeyProbeResult } from "@/lib/types";

export function ProviderKeyList({
  keys,
  onChanged,
}: {
  keys: ProviderKey[];
  onChanged: () => void | Promise<void>;
}) {
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // v0.8.5: saved-key retest state (wires up the previously-unused
  // probeSavedProviderKey fn). One probe at a time; results cached per key
  // id so re-opening a row still shows the last outcome.
  const [probingId, setProbingId] = useState<string | null>(null);
  const [probes, setProbes] = useState<Record<string, ProviderKeyProbeResult>>(
    {},
  );
  const [probeErrors, setProbeErrors] = useState<Record<string, string>>({});
  // v1.26.0: per-key model selection modal. Only one open at a time.
  const [selectingKey, setSelectingKey] = useState<ProviderKey | null>(null);

  const onTest = async (k: ProviderKey) => {
    setProbingId(k.id);
    setProbeErrors((prev) => {
      const next = { ...prev };
      delete next[k.id];
      return next;
    });
    try {
      const result = await probeSavedProviderKey(k.id);
      setProbes((prev) => ({ ...prev, [k.id]: result }));
    } catch (err) {
      setProbeErrors((prev) => ({
        ...prev,
        [k.id]: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setProbingId(null);
    }
  };

  const onDelete = async (k: ProviderKey) => {
    if (!confirm(`Delete '${k.name}'? Anything using it will fall back to env defaults.`)) {
      return;
    }
    setDeletingId(k.id);
    setError(null);
    try {
      await deleteProviderKey(k.id);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingId(null);
    }
  };

  if (keys.length === 0) {
    return (
      <p className="rounded-md border border-border bg-secondary/30 p-4 text-sm text-muted-foreground">
        No provider keys configured. Upload a JSON file or paste entries above
        to get started.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {error && <p className="text-sm text-critical">{error}</p>}
      <ul className="divide-y divide-border rounded-md border border-border">
        {keys.map((k) => (
          <li
            key={k.id}
            className="flex items-center justify-between gap-3 px-4 py-3"
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{k.name}</span>
                <Badge variant="outline" className="font-mono text-[10px]">
                  {k.provider}
                </Badge>
                <Badge variant="secondary" className="text-[10px]">
                  {k.kind === "mcp_server" ? "MCP" : "LLM"}
                </Badge>
                {k.is_local && (
                  <Badge variant="outline" className="text-[10px]">
                    local
                  </Badge>
                )}
              </div>
              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                {k.models.length > 0 && (
                  <span>
                    models:{" "}
                    <code className="font-mono">{k.models.join(", ")}</code>
                  </span>
                )}
                {k.endpoint && (
                  <span>
                    endpoint:{" "}
                    <code className="font-mono">{k.endpoint}</code>
                  </span>
                )}
                {k.key_last4 && (
                  <span>
                    key:{" "}
                    <code className="font-mono">••••••••••{k.key_last4}</code>
                  </span>
                )}
                {!k.key_last4 && !k.is_local && (
                  <span className="text-critical">key: (missing)</span>
                )}
              </div>
              {probes[k.id] && (
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                  {probes[k.id].ok ? (
                    <>
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-emerald-600 dark:text-emerald-400">
                        <Check className="h-3 w-3" /> test passed
                      </span>
                      <span className="text-muted-foreground">
                        {probes[k.id].models.length} model{probes[k.id].models.length === 1 ? "" : "s"}
                        {probes[k.id].latency_ms != null
                          ? ` · ${probes[k.id].latency_ms} ms`
                          : ""}
                      </span>
                    </>
                  ) : (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-amber-600 dark:text-amber-400">
                      <Wifi className="h-3 w-3" />
                      {probes[k.id].reachable
                        ? "reachable but rejected"
                        : "unreachable"}
                      {probes[k.id].status_code != null
                        ? ` · HTTP ${probes[k.id].status_code}`
                        : ""}
                    </span>
                  )}
                  {probes[k.id].error && (
                    <span className="text-critical">{probes[k.id].error}</span>
                  )}
                </div>
              )}
              {probeErrors[k.id] && (
                <p className="mt-2 text-xs text-critical">{probeErrors[k.id]}</p>
              )}
            </div>
            <div className="flex items-center gap-1">
              {k.kind !== "mcp_server" && (
                <>
                  <Button
                    size="icon"
                    variant="ghost"
                    disabled={probingId === k.id}
                    onClick={() => onTest(k)}
                    aria-label={`Test ${k.name}`}
                    title="Test this key + endpoint and list available models"
                  >
                    {probingId === k.id ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Wifi className="h-4 w-4" />
                    )}
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={() => setSelectingKey(k)}
                    aria-label={`Select models for ${k.name}`}
                    title="Pick which of this key's models show up in Configurations dropdowns"
                  >
                    <ListChecks className="h-4 w-4" />
                  </Button>
                </>
              )}
              <Button
                size="icon"
                variant="ghost"
                disabled={deletingId === k.id}
                onClick={() => onDelete(k)}
                aria-label={`Delete ${k.name}`}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <SelectModelsModal
        keyRow={selectingKey}
        open={selectingKey !== null}
        onOpenChange={(next) => {
          if (!next) setSelectingKey(null);
        }}
        onSaved={onChanged}
      />
    </div>
  );
}
