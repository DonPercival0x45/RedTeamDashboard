"use client";

// v0.12.0: per-engagement Tools view. Approved tools appear as a
// pickable catalog; selecting one renders a form generated from its
// manifest arg schema, invoke button drops a row into the history
// table below. Detail slide-over shows stdout/stderr from a picked
// history row.

import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Play, Wrench, X } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { invokeTool } from "@/lib/api";
import { qk, useToolInvocations, useTools } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type {
  ToolInvocationRead,
  ToolInvocationStatus,
  ToolRead,
} from "@/lib/types";

interface ManifestArgSpec {
  name: string;
  type: "string" | "integer" | "boolean" | "enum";
  required?: boolean;
  values?: string[];
  description?: string;
}

const STATUS_TONE: Record<ToolInvocationStatus, string> = {
  queued: "border-slate-500/50 bg-slate-500/10 text-slate-200",
  running: "border-amber-500/50 bg-amber-500/10 text-amber-200",
  completed: "border-emerald-500/50 bg-emerald-500/10 text-emerald-200",
  failed: "border-rose-500/50 bg-rose-500/10 text-rose-200",
  timeout: "border-orange-500/50 bg-orange-500/10 text-orange-200",
};

export function ToolsView({ slug }: { slug: string }) {
  // v1.0.0: react-query owns both fetches. Tool catalog is static-ish
  // (no polling). Invocations auto-poll @ 3s while any row is
  // queued/running, then stop — see useToolInvocations().
  const qc = useQueryClient();
  const { data: tools, error: toolsError } = useTools({ status: "approved" });
  const { data: invocations, error: invocationsError } =
    useToolInvocations(slug);

  const [selected, setSelected] = useState<ToolRead | null>(null);
  const [inspecting, setInspecting] = useState<ToolInvocationRead | null>(null);

  const error = toolsError ?? invocationsError;
  const errorMsg = error
    ? error instanceof Error
      ? error.message
      : String(error)
    : null;

  const approved = useMemo(() => tools ?? [], [tools]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Wrench className="h-4 w-4" />
            Tools
          </CardTitle>
          <CardDescription>
            Approved catalog. Pick a tool, fill args, invoke — output lands
            in the history below. All runs execute in a fresh sibling
            container (no state carry between runs). Every invocation is
            audit-logged.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {errorMsg && <p className="mb-2 text-xs text-critical">{errorMsg}</p>}
          {approved.length === 0 && tools !== undefined && (
            <p className="text-xs text-muted-foreground">
              No approved tools yet. An admin registers tools in
              <a
                href="/settings/tools"
                className="ml-1 underline decoration-dotted hover:decoration-solid"
              >
                Settings → Tools
              </a>
              .
            </p>
          )}
          {approved.length > 0 && (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {approved.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setSelected(t)}
                  className={cn(
                    "rounded-md border border-border/60 p-3 text-left transition-colors hover:border-foreground/40 hover:bg-secondary/40",
                    selected?.id === t.id &&
                      "border-emerald-500/50 bg-emerald-500/5",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{t.name}</span>
                    <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                      {t.kind} · {t.task_kind} · {t.risk_level}
                    </span>
                  </div>
                  {t.description && (
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                      {t.description}
                    </p>
                  )}
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {selected && (
        <ToolInvokeForm
          tool={selected}
          onDone={async (row) => {
            // Prepend to the cached list so the new row is visible immediately;
            // the auto-poll (3s while status=queued/running) then keeps it
            // fresh until it reaches a terminal state.
            qc.setQueryData<ToolInvocationRead[] | undefined>(
              qk.toolInvocations(slug),
              (prev) => (prev ? [row, ...prev] : [row]),
            );
            if (row.status === "completed" || row.status === "failed") {
              setInspecting(row);
            }
          }}
          slug={slug}
        />
      )}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">History</CardTitle>
          <CardDescription>
            Recent invocations against this engagement. Click a row to see
            captured stdout/stderr.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!invocations && <p className="text-xs text-muted-foreground">Loading…</p>}
          {invocations && invocations.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No invocations yet.
            </p>
          )}
          {invocations && invocations.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="border-b border-border/60 text-left text-muted-foreground">
                  <tr>
                    <th className="py-2 pr-3 font-normal">When</th>
                    <th className="py-2 pr-3 font-normal">Tool</th>
                    <th className="py-2 pr-3 font-normal">Status</th>
                    <th className="py-2 pr-3 font-normal">Exit</th>
                    <th className="py-2 pr-3 font-normal">Duration</th>
                    <th className="py-2 pr-3 font-normal">Args</th>
                  </tr>
                </thead>
                <tbody>
                  {invocations.map((inv) => (
                    <tr
                      key={inv.id}
                      onClick={() => setInspecting(inv)}
                      className="cursor-pointer border-b border-border/30 hover:bg-secondary/40 last:border-none"
                    >
                      <td className="py-2 pr-3 font-mono text-[11px] text-muted-foreground">
                        {new Date(inv.started_at).toLocaleTimeString()}
                      </td>
                      <td className="py-2 pr-3">{inv.tool_name}</td>
                      <td className="py-2 pr-3">
                        <span
                          className={cn(
                            "rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
                            STATUS_TONE[inv.status],
                          )}
                        >
                          {inv.status}
                        </span>
                      </td>
                      <td className="py-2 pr-3 font-mono text-[11px]">
                        {inv.exit_code ?? "—"}
                      </td>
                      <td className="py-2 pr-3 text-[11px] text-muted-foreground">
                        {formatDuration(inv.started_at, inv.completed_at)}
                      </td>
                      <td className="py-2 pr-3 font-mono text-[10px] text-muted-foreground">
                        {JSON.stringify(inv.args)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {inspecting && (
        <InvocationInspector
          invocation={inspecting}
          onClose={() => setInspecting(null)}
        />
      )}
    </div>
  );
}

function ToolInvokeForm({
  tool,
  slug,
  onDone,
}: {
  tool: ToolRead;
  slug: string;
  onDone: (row: ToolInvocationRead) => void;
}) {
  const argSpecs: ManifestArgSpec[] = useMemo(() => {
    const spec = (tool.manifest as Record<string, unknown>)?.spec as
      | { args?: ManifestArgSpec[] }
      | undefined;
    return spec?.args ?? [];
  }, [tool.manifest]);

  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Reset args when the selected tool changes
    setValues({});
    setError(null);
  }, [tool.id]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const args: Record<string, unknown> = {};
      for (const spec of argSpecs) {
        const raw = values[spec.name];
        if (raw === undefined || raw === "") continue;
        args[spec.name] = raw;
      }
      const row = await invokeTool(slug, tool.id, args);
      onDone(row);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Invoke: {tool.name}</CardTitle>
        <CardDescription>{tool.description || "No description."}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {argSpecs.length === 0 && (
          <p className="text-xs text-muted-foreground">
            This tool takes no arguments.
          </p>
        )}
        {argSpecs.map((spec) => (
          <div key={spec.name}>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              {spec.name}
              {spec.required && <span className="ml-1 text-rose-400">*</span>}
              <span className="ml-2 text-[10px] uppercase tracking-wide text-muted-foreground/70">
                {spec.type}
              </span>
            </label>
            {spec.type === "enum" ? (
              <select
                value={String(values[spec.name] ?? "")}
                onChange={(e) =>
                  setValues({ ...values, [spec.name]: e.target.value })
                }
                className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
              >
                <option value="">(select)</option>
                {(spec.values ?? []).map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            ) : spec.type === "boolean" ? (
              <input
                type="checkbox"
                checked={Boolean(values[spec.name])}
                onChange={(e) =>
                  setValues({ ...values, [spec.name]: e.target.checked })
                }
                className="accent-emerald-500"
              />
            ) : (
              <input
                type={spec.type === "integer" ? "number" : "text"}
                value={String(values[spec.name] ?? "")}
                onChange={(e) =>
                  setValues({ ...values, [spec.name]: e.target.value })
                }
                className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
              />
            )}
            {spec.description && (
              <p className="mt-0.5 text-[11px] text-muted-foreground/70">
                {spec.description}
              </p>
            )}
          </div>
        ))}

        {error && <p className="text-xs text-critical">{error}</p>}

        <Button size="sm" disabled={busy} onClick={submit}>
          <Play className="mr-1.5 h-3.5 w-3.5" />
          {busy ? "Running…" : "Invoke"}
        </Button>
      </CardContent>
    </Card>
  );
}

function InvocationInspector({
  invocation,
  onClose,
}: {
  invocation: ToolInvocationRead;
  onClose: () => void;
}) {
  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/60"
        onClick={onClose}
        aria-hidden
      />
      <aside className="fixed inset-y-0 right-0 z-50 flex w-full max-w-2xl flex-col overflow-y-auto border-l border-border bg-popover p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs text-muted-foreground">
              {invocation.tool_name} · v{invocation.tool_version} ·{" "}
              {new Date(invocation.started_at).toLocaleString()}
            </div>
            <h2 className="mt-1 flex items-center gap-2 text-lg font-semibold leading-tight">
              {invocation.tool_name}
              <span
                className={cn(
                  "rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
                  STATUS_TONE[invocation.status],
                )}
              >
                {invocation.status}
              </span>
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <section className="mt-4 text-xs text-muted-foreground">
          <p>
            Exit: {invocation.exit_code ?? "—"} ·{" "}
            Duration:{" "}
            {formatDuration(invocation.started_at, invocation.completed_at)}
            {invocation.runtime_ref && (
              <> · Runtime: {invocation.runtime_ref}</>
            )}
          </p>
        </section>

        <section className="mt-4">
          <h3 className="text-sm font-medium">Args</h3>
          <pre className="mt-2 rounded-md border border-border bg-background p-3 font-mono text-[11px] text-muted-foreground">
            {JSON.stringify(invocation.args, null, 2)}
          </pre>
        </section>

        {invocation.error && (
          <section className="mt-4">
            <h3 className="text-sm font-medium text-critical">Runner error</h3>
            <pre className="mt-2 rounded-md border border-critical/50 bg-critical/5 p-3 font-mono text-[11px] text-critical">
              {invocation.error}
            </pre>
          </section>
        )}

        <section className="mt-4">
          <h3 className="text-sm font-medium">stdout</h3>
          <pre className="mt-2 max-h-96 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-[11px]">
            {invocation.stdout || "(empty)"}
          </pre>
        </section>

        <section className="mt-4">
          <h3 className="text-sm font-medium">stderr</h3>
          <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-[11px]">
            {invocation.stderr || "(empty)"}
          </pre>
        </section>
      </aside>
    </>
  );
}

function formatDuration(startedAt: string, completedAt: string | null): string {
  if (!completedAt) return "…";
  const ms = new Date(completedAt).getTime() - new Date(startedAt).getTime();
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}
