"use client";

// v1.4.11: per-analyst default model (roadmap #3 / #12). The analyst
// picks a provider + model once on the Keys settings page; the
// Start-a-run prompt pre-selects it on every engagement instead of
// resetting to the hardcoded Anthropic default each run.

import { useEffect, useMemo, useState } from "react";
import { Check, Loader2, Wifi } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useMe,
  useProviderKeys,
  useUpdateMyPreferencesMutation,
} from "@/lib/hooks";
import { probeSavedProviderKey } from "@/lib/api";
import { PROVIDER_PRESETS } from "@/lib/llm-providers";
import type { ProviderKeyProbeResult } from "@/lib/types";

export function DefaultModelCard() {
  const { data: me } = useMe();
  const mutation = useUpdateMyPreferencesMutation();

  const initialProvider = me?.default_llm_provider ?? "";
  const initialModel = me?.default_llm_model ?? "";

  const [provider, setProvider] = useState(initialProvider);
  const [model, setModel] = useState(initialModel);
  const [error, setError] = useState<string | null>(null);
  // v0.19.0 (roadmap #27): model discovery. Probe the analyst's cached
  // key for the selected provider and feed the live model catalog into a
  // <datalist> on the model input, so they pick from what actually works
  // instead of hand-typing a model string.
  const { data: keys } = useProviderKeys();
  const matchingKey = useMemo(
    () =>
      (keys ?? []).find(
        (k) =>
          k.provider === provider && k.kind === "model_provider",
      ),
    [keys, provider],
  );
  const [probe, setProbe] = useState<ProviderKeyProbeResult | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);

  // Reset probe state when the provider (and thus the key) changes.
  useEffect(() => {
    setProbe(null);
    setProbeError(null);
  }, [matchingKey?.id]);

  // v1.25.2: auto-probe the moment a cached key becomes available for
  // the selected provider — no more clicking Discover. The stored
  // models list on the key is a fallback if the probe fails.
  useEffect(() => {
    let cancelled = false;
    if (!matchingKey || probe) return;
    (async () => {
      setProbing(true);
      setProbeError(null);
      try {
        const result = await probeSavedProviderKey(matchingKey.id);
        if (!cancelled) setProbe(result);
      } catch (err) {
        if (!cancelled) {
          setProbeError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!cancelled) setProbing(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [matchingKey, probe]);

  const discoveredModels = useMemo(() => {
    const fromProbe = probe?.ok ? probe.models : [];
    const fromKey = matchingKey?.models ?? [];
    // dedupe, preserve order (probe first, then any stored on the key)
    const seen = new Set<string>();
    const out: string[] = [];
    for (const m of [...fromProbe, ...fromKey]) {
      if (m && !seen.has(m)) {
        seen.add(m);
        out.push(m);
      }
    }
    return out;
  }, [probe, matchingKey]);

  const onDiscover = async () => {
    if (!matchingKey) return;
    setProbing(true);
    setProbeError(null);
    try {
      const result = await probeSavedProviderKey(matchingKey.id);
      setProbe(result);
    } catch (err) {
      setProbeError(err instanceof Error ? err.message : String(err));
    } finally {
      setProbing(false);
    }
  };

  // Sync local state once the cached default loads (useMe is async).
  useEffect(() => {
    setProvider(me?.default_llm_provider ?? "");
    setModel(me?.default_llm_model ?? "");
  }, [me?.default_llm_provider, me?.default_llm_model]);

  const dirty =
    provider !== initialProvider || model !== initialModel;

  const onSave = async () => {
    setError(null);
    try {
      await mutation.mutateAsync({
        default_llm_provider: provider || null,
        default_llm_model: model.trim() || null,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const onClear = async () => {
    setError(null);
    setProvider("");
    setModel("");
    try {
      await mutation.mutateAsync({
        default_llm_provider: null,
        default_llm_model: null,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Default model</CardTitle>
        <CardDescription>
          Pre-selected on the Start-a-run prompt for every engagement.
          Leave blank to keep the built-in default.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-2">
          <Label htmlFor="dm-provider" className="text-xs">
            Provider
          </Label>
          <select
            id="dm-provider"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm"
          >
            <option value="">(use built-in default)</option>
            {PROVIDER_PRESETS.filter(
              (p) => (p.kind ?? "model_provider") === "model_provider",
            ).map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="dm-model" className="text-xs">
            Model
          </Label>
          <div className="flex items-center gap-2">
            {discoveredModels.length > 0 ? (
              // v1.25.2: proper dropdown once models are discovered. The
              // typed value stays selectable as "<name> (custom)" so an
              // analyst who wants a specific revision or alias not in
              // the live catalog isn't forced back to free-text.
              <select
                id="dm-model"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1.5 font-mono text-sm"
              >
                <option value="">— use built-in default —</option>
                {discoveredModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
                {model && !discoveredModels.includes(model) && (
                  <option value={model}>{model} (custom)</option>
                )}
              </select>
            ) : (
              <Input
                id="dm-model"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="e.g. gpt-4o, claude-opus-4-7, llama3.1:8b"
                className="font-mono text-sm"
              />
            )}
            {matchingKey && (
              <Button
                size="sm"
                variant="outline"
                className="shrink-0"
                disabled={probing}
                onClick={onDiscover}
                title="Probe your cached key and list the models it actually serves"
              >
                {probing ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Wifi className="mr-1 h-3.5 w-3.5" />
                )}
                {discoveredModels.length > 0 ? "Refresh" : "Discover"}
              </Button>
            )}
          </div>
          {/* discovery status / guidance */}
          {probe?.ok && (
            <p className="text-[11px] text-emerald-600 dark:text-emerald-400">
              {probe.models.length} model{probe.models.length === 1 ? "" : "s"} found
              {probe.latency_ms != null ? ` · ${probe.latency_ms} ms` : ""}{" "}
              — pick from the dropdown or keep typing.
            </p>
          )}
          {probe && !probe.ok && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              {probe.reachable ? "reachable but rejected" : "unreachable"}
              {probe.status_code != null ? ` · HTTP ${probe.status_code}` : ""}
              {probe.error ? ` · ${probe.error}` : ""}
            </p>
          )}
          {probeError && (
            <p className="text-[11px] text-critical">{probeError}</p>
          )}
          {!matchingKey && provider && (
            <p className="text-[11px] text-muted-foreground">
              No cached key for “{provider}” — add one above to discover models.
            </p>
          )}
        </div>
        {error && <p className="text-sm text-critical">{error}</p>}
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            onClick={onSave}
            disabled={mutation.isPending || !dirty}
          >
            <Check className="mr-1.5 h-3.5 w-3.5" />
            {mutation.isPending ? "Saving…" : "Save default"}
          </Button>
          {(initialProvider || initialModel) && (
            <Button
              size="sm"
              variant="outline"
              onClick={onClear}
              disabled={mutation.isPending}
            >
              Clear
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
