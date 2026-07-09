"use client";

// v1.24.0: per-analyst per-engagement agent-model routing.
//
// UI shape:
//   Left  — engagement picker (list of every engagement the analyst has
//           access to; each row shows a small pinned/unpinned indicator).
//   Right — three model dropdowns (Strategic / Tactical / Correlate) for
//           the currently-selected engagement. Dropdown options are the
//           union of the analyst's cached ProviderKey.models[]. Empty
//           state prompts them to cache a key first.
//
// Sidebar buttons — Export All (JSON download) + Import (file picker +
// preview modal → Apply).
//
// Per-analyst isolation is enforced server-side; this UI never sees
// another analyst's configurations.
import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { AlertCircle, Download, SlidersHorizontal, Trash2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  useAgentConfigurations,
  useClearAgentConfigurationMutation,
  useDownloadAgentConfigurations,
  useEngagements,
  useImportAgentConfigurationsMutation,
  usePutAgentConfigurationMutation,
  useProviderKeys,
} from "@/lib/hooks";
import type {
  AgentConfigExport,
  AgentConfigRead,
  ConfigurableAgentRole,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const ROLES: {
  key: ConfigurableAgentRole;
  label: string;
  hint: string;
}[] = [
  {
    key: "strategic",
    label: "Strategic",
    hint: "Analyzes findings and suggests scan / enum tasks.",
  },
  {
    key: "tactical",
    label: "Tactical",
    hint: "Dispatches accepted tasks to the worker run stream.",
  },
  {
    key: "correlate",
    label: "Correlate",
    hint: "Clusters related findings for report roll-up.",
  },
];

export default function SettingsConfigurationsPage() {
  const engagementsQ = useEngagements();
  const configsQ = useAgentConfigurations();
  const providerKeysQ = useProviderKeys();
  const putMut = usePutAgentConfigurationMutation();
  const clearMut = useClearAgentConfigurationMutation();
  const downloadMut = useDownloadAgentConfigurations();
  const importMut = useImportAgentConfigurationsMutation();

  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [importPreview, setImportPreview] = useState<AgentConfigExport | null>(
    null,
  );
  const [importError, setImportError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const engagements = engagementsQ.data ?? [];
  const configs = configsQ.data?.configurations ?? [];
  const configsBySlug = useMemo(() => {
    const map = new Map<string, AgentConfigRead>();
    for (const c of configs) map.set(c.engagement_slug, c);
    return map;
  }, [configs]);

  // Union of models across every cached provider key. Deduped.
  const availableModels = useMemo(() => {
    const set = new Set<string>();
    for (const key of providerKeysQ.data ?? []) {
      for (const m of key.models ?? []) set.add(m);
    }
    return Array.from(set).sort();
  }, [providerKeysQ.data]);

  const selectedEngagement =
    engagements.find((e) => e.slug === selectedSlug) ?? engagements[0] ?? null;
  const activeSlug = selectedEngagement?.slug ?? null;
  const activeConfig = activeSlug ? configsBySlug.get(activeSlug) : undefined;

  const isBusy = putMut.isPending || clearMut.isPending;

  const setRoleModel = (role: ConfigurableAgentRole, value: string) => {
    if (!activeSlug) return;
    // Empty string in the picker means "clear this role".
    const body = { [role]: value === "" ? null : value };
    putMut.mutate({ slug: activeSlug, body });
  };

  const onImportPick = (file: File) => {
    setImportError(null);
    file
      .text()
      .then((txt) => {
        const parsed = JSON.parse(txt);
        if (
          typeof parsed?.version !== "number" ||
          typeof parsed?.configurations !== "object" ||
          parsed.configurations === null
        ) {
          throw new Error("File is not an RTD agent-configuration export.");
        }
        setImportPreview(parsed as AgentConfigExport);
      })
      .catch((err: unknown) => {
        setImportError(err instanceof Error ? err.message : String(err));
      });
  };

  const applyImport = () => {
    if (!importPreview) return;
    importMut.mutate(importPreview, {
      onSuccess: () => {
        setImportPreview(null);
      },
    });
  };

  return (
    <div className="mx-auto max-w-5xl space-y-8 px-4 py-6">
      <div>
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← engagements
        </Link>
        <h1 className="mt-2 flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <SlidersHorizontal className="h-6 w-6" />
          Configurations
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Pin which LLM model each agent uses per engagement. Choices are
          per-analyst — Kendall&apos;s pins don&apos;t affect yours. The
          provider key comes from the ephemeral BYO cache on the analyst
          who kicks a run.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => downloadMut.mutate()}
          disabled={downloadMut.isPending || configs.length === 0}
        >
          <Download className="mr-2 h-4 w-4" />
          Export all
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => fileRef.current?.click()}
          disabled={importMut.isPending}
        >
          <Upload className="mr-2 h-4 w-4" />
          Import
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onImportPick(f);
            // Reset so re-uploading the same file fires the change handler.
            e.target.value = "";
          }}
        />
        <span className="text-xs text-muted-foreground">
          {configs.length} configured{" "}
          {configs.length === 1 ? "engagement" : "engagements"}
        </span>
      </div>

      {importError && (
        <div className="flex items-start gap-2 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-900 dark:text-amber-200">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{importError}</span>
        </div>
      )}

      {availableModels.length === 0 && (
        <div className="rounded border border-dashed border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
          No models discovered yet. Cache a provider key at{" "}
          <Link
            href="/settings/keys"
            className="text-primary hover:underline"
          >
            Provider keys
          </Link>{" "}
          and hit &quot;Discover&quot; on a key so its models show up in the
          dropdowns.
        </div>
      )}

      <div className="grid gap-6 md:grid-cols-[minmax(220px,280px)_1fr]">
        <aside className="space-y-1 rounded border border-border bg-card p-2">
          {engagements.length === 0 && (
            <div className="px-2 py-4 text-sm text-muted-foreground">
              No engagements yet.
            </div>
          )}
          {engagements.map((eng) => {
            const cfg = configsBySlug.get(eng.slug);
            const pinnedCount = cfg
              ? [cfg.strategic, cfg.tactical, cfg.correlate].filter(Boolean)
                  .length
              : 0;
            const isActive = eng.slug === activeSlug;
            return (
              <button
                key={eng.id}
                type="button"
                onClick={() => setSelectedSlug(eng.slug)}
                className={cn(
                  "flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-sm",
                  isActive
                    ? "bg-primary/10 text-foreground"
                    : "hover:bg-muted/60 text-muted-foreground",
                )}
              >
                <span className="truncate">{eng.name}</span>
                {pinnedCount > 0 && (
                  <span
                    className="ml-2 shrink-0 rounded bg-primary/20 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-primary"
                    title={`${pinnedCount} of 3 agents pinned`}
                  >
                    {pinnedCount}/3
                  </span>
                )}
              </button>
            );
          })}
        </aside>

        <section className="space-y-4">
          {selectedEngagement === null && (
            <div className="rounded border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
              Pick an engagement to configure.
            </div>
          )}
          {selectedEngagement && (
            <>
              <div className="rounded border border-border bg-card p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="text-base font-medium">
                      {selectedEngagement.name}
                    </div>
                    <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                      {selectedEngagement.slug}
                    </div>
                  </div>
                  {activeConfig && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      disabled={isBusy}
                      onClick={() => {
                        if (activeSlug) clearMut.mutate(activeSlug);
                      }}
                    >
                      <Trash2 className="mr-2 h-4 w-4" />
                      Clear all
                    </Button>
                  )}
                </div>

                <div className="mt-4 grid gap-4">
                  {ROLES.map((role) => {
                    const current =
                      (activeConfig?.[role.key] as string | null | undefined) ??
                      "";
                    return (
                      <div key={role.key} className="grid gap-1">
                        <label
                          htmlFor={`agent-cfg-${role.key}`}
                          className="text-sm font-medium"
                        >
                          {role.label}
                        </label>
                        <p className="text-xs text-muted-foreground">
                          {role.hint}
                        </p>
                        <select
                          id={`agent-cfg-${role.key}`}
                          value={current}
                          disabled={isBusy || availableModels.length === 0}
                          onChange={(e) => setRoleModel(role.key, e.target.value)}
                          className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
                        >
                          <option value="">
                            {availableModels.length === 0
                              ? "no models discovered"
                              : "— use default —"}
                          </option>
                          {availableModels.map((m) => (
                            <option key={m} value={m}>
                              {m}
                            </option>
                          ))}
                          {current && !availableModels.includes(current) && (
                            // Legacy or imported value that isn't in the
                            // current model list — keep it selectable so
                            // it doesn't silently vanish from the picker.
                            <option value={current}>{current} (custom)</option>
                          )}
                        </select>
                      </div>
                    );
                  })}
                </div>
              </div>
            </>
          )}
        </section>
      </div>

      {importPreview && (
        <ImportPreviewModal
          preview={importPreview}
          engagementSlugs={new Set(engagements.map((e) => e.slug))}
          onCancel={() => setImportPreview(null)}
          onConfirm={applyImport}
          pending={importMut.isPending}
        />
      )}
    </div>
  );
}

function ImportPreviewModal({
  preview,
  engagementSlugs,
  onCancel,
  onConfirm,
  pending,
}: {
  preview: AgentConfigExport;
  engagementSlugs: Set<string>;
  onCancel: () => void;
  onConfirm: () => void;
  pending: boolean;
}) {
  const entries = Object.entries(preview.configurations);
  const knownEntries = entries.filter(([slug]) => engagementSlugs.has(slug));
  const unknownEntries = entries.filter(([slug]) => !engagementSlugs.has(slug));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-2xl rounded-lg border border-border bg-card p-6 shadow-lg">
        <h2 className="text-lg font-semibold">Import agent configurations</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Exported {new Date(preview.exported_at).toLocaleString()} — will
          overwrite existing pins for {knownEntries.length}{" "}
          {knownEntries.length === 1 ? "engagement" : "engagements"}.
        </p>

        {knownEntries.length > 0 && (
          <div className="mt-4">
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Will apply
            </div>
            <ul className="max-h-64 overflow-auto rounded border border-border bg-background text-sm">
              {knownEntries.map(([slug, payload]) => (
                <li
                  key={slug}
                  className="border-b border-border/40 px-3 py-2 last:border-b-0"
                >
                  <div className="font-mono text-xs text-muted-foreground">
                    {slug}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs">
                    {payload.strategic && (
                      <span>
                        <span className="text-muted-foreground">strategic</span>{" "}
                        · {payload.strategic}
                      </span>
                    )}
                    {payload.tactical && (
                      <span>
                        <span className="text-muted-foreground">tactical</span>{" "}
                        · {payload.tactical}
                      </span>
                    )}
                    {payload.correlate && (
                      <span>
                        <span className="text-muted-foreground">correlate</span>{" "}
                        · {payload.correlate}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {unknownEntries.length > 0 && (
          <div className="mt-4">
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-amber-700 dark:text-amber-400">
              Skipping {unknownEntries.length} unknown slug
              {unknownEntries.length === 1 ? "" : "s"}
            </div>
            <ul className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 font-mono text-xs">
              {unknownEntries.map(([slug]) => (
                <li key={slug}>{slug}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={onCancel}
            disabled={pending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={pending || knownEntries.length === 0}
          >
            {pending
              ? "Applying…"
              : `Apply ${knownEntries.length} config${knownEntries.length === 1 ? "" : "s"}`}
          </Button>
        </div>
      </div>
    </div>
  );
}
