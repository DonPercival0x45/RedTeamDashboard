"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Loader2, Zap } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";
import { startRun } from "@/lib/api";
import { useMe, useProviderKeys, useScope } from "@/lib/hooks";
import { CUSTOM_VALUE, getPresetModels } from "@/lib/llm-providers";
import { runSlugFromId, useRunToast } from "@/components/run-toast-provider";
import { useRegisterRunPromptTarget } from "@/components/run-prompt-context";
import type { LLMProvider, ScopeItem, ScopeKind } from "@/lib/types";

interface LastDispatched {
  threadId: string;
  provider: LLMProvider;
  modelName: string;
  at: number;
}

// Default model names by provider. Pre-filled when the user picks a
// provider; the actual model name is free-form (no backend whitelist) so
// rotating to a new release is just a text edit, not a code change.
// v0.8.1: list mirrors the /settings/keys Quick Add presets so an
// analyst's stored key always has a matching dropdown entry here.
const DEFAULT_MODELS: Record<LLMProvider, string> = {
  anthropic: "claude-opus-4-7",
  openai: "gpt-4o-mini",
  azure: "",
  ollama: "llama3.1:8b",
  google: "gemini-2.0-pro",
  xai: "grok-3",
  mistral: "mistral-large-latest",
  cohere: "command-r-plus",
  together: "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
  groq: "llama-3.3-70b-versatile",
  deepseek: "deepseek-chat",
  custom: "",
};

const PROVIDER_OPTIONS: { value: LLMProvider; label: string }[] = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google (Gemini)" },
  { value: "azure", label: "Azure OpenAI" },
  { value: "xai", label: "xAI (Grok)" },
  { value: "mistral", label: "Mistral" },
  { value: "cohere", label: "Cohere" },
  { value: "together", label: "Together AI" },
  { value: "groq", label: "Groq" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "ollama", label: "Ollama (local)" },
  { value: "custom", label: "Custom (OpenAI-compatible)" },
];

// v1.4.5: scope-derived quick-action chips (roadmap #2, "Scope Fix").
// Each actionable (non-exclusion) scope item maps to a canned first-move
// prompt; clicking a chip prefills the prompt so the analyst reviews and
// hits Start. Batch "fire all" is intentionally deferred — the roadmap
// flags its concurrency / LLM-cost concerns and those need a cap +
// confirmation step before shipping.
const SCOPE_ACTION_TEMPLATES: Record<ScopeKind, (value: string) => string> = {
  domain: (v) =>
    `Enumerate subdomains, DNS records, and certificate-transparency logs for ${v}, then probe what's live.`,
  ip: (v) =>
    `Run port discovery and service detection against ${v}, then enumerate any open services.`,
  cidr: (v) =>
    `Discover live hosts in ${v} and enumerate open ports and services across the range.`,
  url: (v) =>
    `Recon and probe ${v}: fingerprint the stack, enumerate paths, and surface anything notable.`,
};

export function RunPrompt({
  slug,
  onStarted,
}: {
  slug: string;
  onStarted?: (threadId: string) => void;
}) {
  const [prompt, setPrompt] = useState(
    "enumerate acme.com subdomains and probe what's live",
  );
  // v1.11.0: expose setPrompt to the ToolsPanel bridge so tool buttons
  // can drop their example prompt into the textarea without either
  // component owning the other's state.
  useRegisterRunPromptTarget(setPrompt);
  const { data: scopeItems } = useScope(slug);
  const scopeActions = useMemo<ScopeItem[]>(
    () => (scopeItems ?? []).filter((s) => !s.is_exclusion),
    [scopeItems],
  );
  const [provider, setProvider] = useState<LLMProvider>("anthropic");
  // v0.8.3: model is now a hybrid dropdown. ``modelSelect`` is what's
  // selected in the <select>; CUSTOM_VALUE means the analyst chose
  // "Custom…" and the actual string lives in ``customModel``. The
  // ``modelName`` derived value is what we send to the API.
  const [modelSelect, setModelSelect] = useState<string>(
    DEFAULT_MODELS.anthropic,
  );
  const [customModel, setCustomModel] = useState<string>("");
  // v1.0.0: shared useProviderKeys cache. The /settings/keys page hits the
  // same key, so the two share one round-trip. Best-effort: on 401 / Redis
  // miss, `keys` stays [] and the dropdown falls back to presets.
  const { data: keys = [] } = useProviderKeys();
  // v1.4.11: pre-select the analyst's saved default model (roadmap #3 / #12)
  // instead of the hardcoded Anthropic default. Fires once when /me loads.
  const { data: me } = useMe();
  useEffect(() => {
    if (!me) return;
    const dp = me.default_llm_provider;
    const dm = me.default_llm_model;
    if (!dp && !dm) return;
    const nextProvider = (dp as LLMProvider) || provider;
    if (dp) setProvider(nextProvider);
    if (dm) {
      const presets = getPresetModels(nextProvider);
      if (presets.includes(dm)) {
        setModelSelect(dm);
      } else {
        setModelSelect(CUSTOM_VALUE);
        setCustomModel(dm);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me?.default_llm_provider, me?.default_llm_model]);
  const runToast = useRunToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastDispatched, setLastDispatched] = useState<LastDispatched | null>(
    null,
  );

  // Auto-dismiss the success banner after 12s so it doesn't sit stale forever.
  // 12s is long enough for the analyst to read + click the Status link.
  useEffect(() => {
    if (!lastDispatched) return;
    const t = setTimeout(() => setLastDispatched(null), 12_000);
    return () => clearTimeout(t);
  }, [lastDispatched]);

  // Hybrid model-list build, recomputed on provider or keys change:
  //   stored = models the analyst saved with their BYO key for THIS provider
  //   presets = the built-in default list (PROVIDER_PRESETS.modelsDefault)
  //   custom  = always last so the analyst can type anything
  const { storedModels, presetModels } = useMemo(() => {
    const stored = new Set<string>();
    for (const k of keys) {
      if (k.provider === provider) {
        for (const m of k.models ?? []) {
          if (m && m.trim()) stored.add(m);
        }
      }
    }
    const storedArr = Array.from(stored);
    const presets = getPresetModels(provider).filter(
      (m) => !stored.has(m),
    );
    return { storedModels: storedArr, presetModels: presets };
  }, [keys, provider]);

  const isCustom = modelSelect === CUSTOM_VALUE;
  const effectiveModel = isCustom ? customModel.trim() : modelSelect;

  const onProviderChange = (next: LLMProvider) => {
    setProvider(next);
    // Pick a sensible default for the new provider: first stored, else
    // first preset, else fall straight into Custom (the textbox).
    const storedForNext = keys
      .filter((k) => k.provider === next)
      .flatMap((k) => k.models ?? [])
      .filter((m) => !!m.trim());
    const presetForNext = getPresetModels(next);
    if (storedForNext.length > 0) {
      setModelSelect(storedForNext[0]);
    } else if (presetForNext.length > 0) {
      setModelSelect(presetForNext[0]);
    } else {
      setModelSelect(CUSTOM_VALUE);
      setCustomModel(DEFAULT_MODELS[next] || "");
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!prompt.trim()) return;
    if (!effectiveModel) {
      setError("model name is required");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await startRun(slug, {
        prompt: prompt.trim(),
        model: { provider, name: effectiveModel },
      });
      setLastDispatched({
        threadId: result.thread_id,
        provider,
        modelName: effectiveModel,
        at: Date.now(),
      });
      // v1.2.0: cross-portal run tracking. Toast is redundant with the
      // inline success banner right here — but the analyst may click
      // away before it settles, and the toast + Status card show the
      // same rt-XXXX handle so the trail is picked up wherever they
      // land next.
      runToast.fire({
        kind: "agent",
        runSlug: runSlugFromId(result.thread_id),
        label: "Run dispatched",
        sublabel: prompt.trim().slice(0, 80),
        openHref: `/e/${slug}?run=${result.thread_id}`,
      });
      onStarted?.(result.thread_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Start a run</CardTitle>
        <CardDescription>
          Pushes <code>run.start</code> onto the inbound stream. The worker
          picks it up; events stream into the panels below.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="prompt">Prompt</Label>
            <Textarea
              id="prompt"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={3}
            />
            {scopeActions.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 pt-1">
                <span className="text-xs text-muted-foreground">
                  Quick actions:
                </span>
                {scopeActions.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() =>
                      setPrompt(
                        SCOPE_ACTION_TEMPLATES[item.kind](item.value),
                      )
                    }
                    className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:border-foreground/40 hover:bg-secondary hover:text-foreground"
                    title={`Prefill a run for ${item.value}`}
                  >
                    <Zap className="h-3 w-3" />
                    <span className="font-mono">{item.kind}</span>
                    <span className="max-w-[12rem] truncate">{item.value}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="provider">Provider</Label>
              <select
                id="provider"
                value={provider}
                onChange={(event) =>
                  onProviderChange(event.target.value as LLMProvider)
                }
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                {PROVIDER_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="model-name">Model</Label>
              <select
                id="model-name"
                value={modelSelect}
                onChange={(event) => setModelSelect(event.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                {storedModels.length > 0 && (
                  <optgroup label="From your keys">
                    {storedModels.map((m) => (
                      <option key={`stored-${m}`} value={m}>
                        {m}
                      </option>
                    ))}
                  </optgroup>
                )}
                {presetModels.length > 0 && (
                  <optgroup label="Defaults">
                    {presetModels.map((m) => (
                      <option key={`preset-${m}`} value={m}>
                        {m}
                      </option>
                    ))}
                  </optgroup>
                )}
                <option value={CUSTOM_VALUE}>Custom…</option>
              </select>
              {isCustom && (
                <Input
                  value={customModel}
                  onChange={(event) => setCustomModel(event.target.value)}
                  placeholder={
                    DEFAULT_MODELS[provider] ||
                    (provider === "azure"
                      ? "deployment name"
                      : "model identifier")
                  }
                  autoFocus
                />
              )}
            </div>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}

          {lastDispatched && (
            <div
              role="status"
              className="flex items-start gap-2 rounded-md border border-emerald-500/50 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-800 dark:text-emerald-100"
            >
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-300" />
              <div className="min-w-0 flex-1">
                <p className="font-medium text-emerald-900 dark:text-emerald-50">
                  Run dispatched to the worker.
                </p>
                <p className="mt-0.5 text-xs text-emerald-700 dark:text-emerald-200/80">
                  thread{" "}
                  <code className="font-mono">
                    {lastDispatched.threadId.slice(0, 8)}
                  </code>{" "}
                  · {lastDispatched.provider}/{lastDispatched.modelName}. Watch
                  the <strong>Status</strong> tab or the event log below for
                  agent calls + approval gates as they fire.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setLastDispatched(null)}
                className="text-xs text-emerald-600 dark:text-emerald-300/70 hover:text-emerald-800 dark:text-emerald-100"
                aria-label="Dismiss"
              >
                ×
              </button>
            </div>
          )}

          <Button type="submit" disabled={busy} className="gap-1.5">
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {busy ? "Dispatching to worker…" : "Run"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
