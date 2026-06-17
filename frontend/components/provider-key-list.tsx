"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { deleteProviderKey } from "@/lib/api";
import type { ProviderKey } from "@/lib/types";

export function ProviderKeyList({
  keys,
  onChanged,
}: {
  keys: ProviderKey[];
  onChanged: () => void | Promise<void>;
}) {
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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
            </div>
            <Button
              size="icon"
              variant="ghost"
              disabled={deletingId === k.id}
              onClick={() => onDelete(k)}
              aria-label={`Delete ${k.name}`}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
