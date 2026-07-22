"use client";

// Quick-add — one paste field for the API key, dropdowns for provider and
// kind, name auto-derives from `<provider> · <last4>`. Lives next to the
// JSON bulk importer; aimed at the "I copied a key from the console, drop
// it in" flow.

import { useMemo, useState } from "react";
import { Check, Loader2, Plus, Wifi } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createProviderKey, probeUnsavedProviderKey } from "@/lib/api";
import {
  CUSTOM_VALUE,
  PROVIDER_PRESETS,
  type ProviderPreset,
} from "@/lib/llm-providers";
import type { ProviderKey, ProviderKeyProbeResult } from "@/lib/types";

type Kind = "model_provider" | "mcp_server" | "other";

// v0.8.3: PROVIDER_PRESETS + CUSTOM_VALUE lifted to lib/llm-providers.ts so
// RunPrompt and QuickAddKey share one source of truth. Local alias kept for
// the limited downstream call sites that referenced the old `Preset` name.
type Preset = ProviderPreset;

export function QuickAddKey({
  onCreated,
}: {
  onCreated: (created: ProviderKey) => void;
}) {
  const [presetSlug, setPresetSlug] = useState<string>(
    PROVIDER_PRESETS[0].slug,
  );
  const [customProvider, setCustomProvider] = useState("");
  const [kind, setKind] = useState<Kind>("model_provider");
  const [apiKey, setApiKey] = useState("");
  // v2.25.0: two-field credential for WiGLE (name + token). Combined into
  // a JSON blob at submit and shipped in ``api_key`` so the backend contract
  // (single string) doesn't change.
  const [wigleName, setWigleName] = useState("");
  const [wigleToken, setWigleToken] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // v0.8.4: Test-key probe state. ``probeResult`` reflects the most recent
  // Test click — its models list is auto-applied to the entry that gets
  // saved (overriding preset defaults), and the ok/reachable flags drive
  // the inline status pill below the Test button.
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<ProviderKeyProbeResult | null>(
    null,
  );
  const [probeError, setProbeError] = useState<string | null>(null);

  const preset = useMemo(
    () => PROVIDER_PRESETS.find((p) => p.slug === presetSlug),
    [presetSlug],
  );
  const isCustom = presetSlug === CUSTOM_VALUE;
  const isMcp = kind === "mcp_server";
  const isToolSecret = preset?.kind === "tool_secret";
  const isMultiCred = preset?.credentialShape === "name+token";
  const effectiveProvider = isCustom
    ? customProvider.trim().toLowerCase()
    : presetSlug;
  // MCP server entries always need an endpoint; otherwise it's preset-driven.
  const endpointRequired = isMcp || preset?.endpointRequired === true;
  const showEndpoint = endpointRequired || isCustom || preset?.endpoint;

  const apiKeyRequired = !preset?.isLocal || isCustom || isMcp;
  // v2.25.0: WiGLE (name+token) — pack the two inputs into a JSON blob so
  // downstream code paths (probe, submit, defaultName last-4) see the same
  // shape they always have. Backend tool already accepts JSON strings.
  const effectiveApiKey = isMultiCred
    ? wigleName.trim() && wigleToken.trim()
      ? JSON.stringify({ name: wigleName.trim(), token: wigleToken.trim() })
      : ""
    : apiKey;
  const multiCredComplete = isMultiCred
    ? wigleName.trim().length > 0 && wigleToken.trim().length > 0
    : true;

  const defaultName = useMemo(() => {
    if (!effectiveProvider) return "";
    // Multi-cred: prefer the raw token tail (analyst recognizes it) over the
    // JSON-encoded api_key blob, which is unreadable.
    const source = isMultiCred ? wigleToken : apiKey;
    const tail = source.length >= 4 ? source.slice(-4) : "";
    return tail
      ? `${effectiveProvider} · …${tail}`
      : `${effectiveProvider}`;
  }, [effectiveProvider, apiKey, isMultiCred, wigleToken]);

  const canTest =
    !!effectiveProvider &&
    !probing &&
    (apiKeyRequired ? !!effectiveApiKey : true) &&
    (endpointRequired ? !!endpoint.trim() : true);

  const onTest = async () => {
    setProbeError(null);
    setProbeResult(null);
    if (!effectiveProvider) {
      setProbeError("Provider is required.");
      return;
    }
    if (apiKeyRequired && !effectiveApiKey) {
      setProbeError(
        isMultiCred
          ? "Both credentials are required to test this provider."
          : "API key is required to test this provider.",
      );
      return;
    }
    if (endpointRequired && !endpoint.trim()) {
      setProbeError("Endpoint is required to test this provider.");
      return;
    }
    setProbing(true);
    try {
      const result = await probeUnsavedProviderKey({
        name: (name.trim() || defaultName).trim() || "probe",
        provider: effectiveProvider,
        kind,
        is_local: preset?.isLocal ?? false,
        endpoint: endpoint.trim() || null,
        api_key: effectiveApiKey || null,
      });
      setProbeResult(result);
    } catch (err) {
      setProbeError(err instanceof Error ? err.message : String(err));
    } finally {
      setProbing(false);
    }
  };

  const onSubmit = async () => {
    setError(null);
    if (!effectiveProvider) {
      setError("Provider is required.");
      return;
    }
    if (apiKeyRequired && !effectiveApiKey) {
      setError(
        isMultiCred
          ? "Both credentials are required for this provider."
          : "API key is required for this provider.",
      );
      return;
    }
    if (endpointRequired && !endpoint.trim()) {
      setError("Endpoint is required for this provider/kind.");
      return;
    }
    // v0.8.4: a fresh probe catalog wins over the preset's defaults when
    // both are available — the analyst just confirmed the live list.
    // v1.25.1: if the analyst didn't hit Test first, auto-probe now so
    // the full model catalog gets stored instead of the 2-model preset
    // default. Failures degrade to the preset default (existing
    // behaviour) — never blocks the save.
    let liveModels = probeResult?.ok ? probeResult.models : null;
    if (liveModels === null) {
      try {
        const auto = await probeUnsavedProviderKey({
          name: (name.trim() || defaultName).trim() || "probe",
          provider: effectiveProvider,
          kind,
          is_local: preset?.isLocal ?? false,
          endpoint: endpoint.trim() || null,
          api_key: effectiveApiKey || null,
        });
        if (auto.ok && auto.models.length > 0) {
          liveModels = auto.models;
        }
      } catch {
        // Probe blew up — fall through to the preset default. The
        // Discover button on the /settings/keys card remains available
        // so the analyst can rehydrate the catalog later.
      }
    }
    setSubmitting(true);
    try {
      const created = await createProviderKey({
        name: (name.trim() || defaultName).trim(),
        provider: effectiveProvider,
        kind,
        is_local: preset?.isLocal ?? false,
        models: liveModels ?? preset?.modelsDefault ?? [],
        endpoint: endpoint.trim() || null,
        api_key: effectiveApiKey || null,
      });
      onCreated(created);
      setApiKey("");
      setWigleName("");
      setWigleToken("");
      setName("");
      setEndpoint("");
      setCustomProvider("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor="qa-provider" className="text-xs">
            Provider
          </Label>
          <select
            id="qa-provider"
            value={presetSlug}
            onChange={(e) => {
              setPresetSlug(e.target.value);
              const p = PROVIDER_PRESETS.find((x) => x.slug === e.target.value);
              setEndpoint(p?.endpoint ?? "");
              // v2.25.0: clear leftover credentials when the shape changes so
              // an unrelated preset's stale input can't accidentally submit.
              setApiKey("");
              setWigleName("");
              setWigleToken("");
              setProbeResult(null);
              setProbeError(null);
              // v2.24.1: auto-flip Kind when the picked preset is a tool
              // secret (freeipapi/ipinfo/wigle) — analysts shouldn't have
              // to remember to flip both. The 'other' kind stores the key
              // as-is; the backend resolver keys by provider slug so it
              // still gets picked up by the tool dispatcher.
              if (p?.kind === "tool_secret") setKind("other");
              else if (kind === "other") setKind("model_provider");
            }}
            disabled={submitting}
            className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          >
            <optgroup label="LLM providers">
              {PROVIDER_PRESETS.filter(
                (p) => (p.kind ?? "model_provider") === "model_provider",
              ).map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.label}
                </option>
              ))}
              <option value={CUSTOM_VALUE}>Custom…</option>
            </optgroup>
            <optgroup label="Enrichment tools (BYO API keys)">
              {PROVIDER_PRESETS.filter(
                (p) => p.kind === "tool_secret",
              ).map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.label}
                </option>
              ))}
            </optgroup>
          </select>
        </div>
        <div>
          <Label htmlFor="qa-kind" className="text-xs">
            Kind
          </Label>
          <select
            id="qa-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as Kind)}
            disabled={submitting}
            className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          >
            <option value="model_provider">Model provider (LLM)</option>
            <option value="mcp_server">MCP server</option>
            <option value="other">Other (stored, not auto-consumed)</option>
          </select>
        </div>
      </div>

      {isCustom && (
        <div>
          <Label htmlFor="qa-custom" className="text-xs">
            Custom provider slug
          </Label>
          <Input
            id="qa-custom"
            value={customProvider}
            onChange={(e) => setCustomProvider(e.target.value)}
            placeholder="e.g. perplexity"
            disabled={submitting}
            className="mt-1"
          />
        </div>
      )}

      {isMultiCred ? (
        <div className="space-y-3">
          <div>
            <Label htmlFor="qa-wigle-name" className="text-xs">
              Encoded for use (name)
            </Label>
            <Input
              id="qa-wigle-name"
              type="text"
              autoComplete="off"
              value={wigleName}
              onChange={(e) => setWigleName(e.target.value)}
              placeholder="AIDxxxxxxxxxxxxxxxxxxxxxxxxx"
              disabled={submitting}
              className="mt-1 font-mono"
            />
          </div>
          <div>
            <Label htmlFor="qa-wigle-token" className="text-xs">
              API token
            </Label>
            <Input
              id="qa-wigle-token"
              type="password"
              autoComplete="off"
              value={wigleToken}
              onChange={(e) => setWigleToken(e.target.value)}
              placeholder="Paste the token"
              disabled={submitting}
              className="mt-1 font-mono"
            />
            {preset?.keyHint && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                {preset.keyHint}
                {preset.signupUrl && (
                  <>
                    {" "}
                    <a
                      href={preset.signupUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="underline hover:text-foreground"
                    >
                      Get them here
                    </a>
                    .
                  </>
                )}
              </p>
            )}
            {isToolSecret && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                Stored per-analyst; the built-in{" "}
                <code className="font-mono">{presetSlug}</code> tool auto-injects
                both credentials at dispatch. Kind is set to <em>other</em> because
                there is no LLM catalog to sync.
              </p>
            )}
          </div>
        </div>
      ) : (
        <div>
          <Label htmlFor="qa-key" className="text-xs">
            API key {!apiKeyRequired && <span className="text-muted-foreground">(optional for local)</span>}
          </Label>
          <Input
            id="qa-key"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={apiKeyRequired ? "Paste the key" : "Leave blank for keyless local runs"}
            disabled={submitting}
            className="mt-1 font-mono"
          />
          {preset?.keyHint && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              {preset.keyHint}
              {preset.signupUrl && (
                <>
                  {" "}
                  <a
                    href={preset.signupUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="underline hover:text-foreground"
                  >
                    Get it here
                  </a>
                  .
                </>
              )}
            </p>
          )}
          {isToolSecret && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              Stored per-analyst; the built-in{" "}
              <code className="font-mono">{presetSlug}</code> tool auto-injects
              it at dispatch. Kind is set to <em>other</em> because there is
              no LLM catalog to sync.
            </p>
          )}
        </div>
      )}

      {showEndpoint && (
        <div>
          <Label htmlFor="qa-endpoint" className="text-xs">
            Endpoint{endpointRequired && " *"}
          </Label>
          <Input
            id="qa-endpoint"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            placeholder={preset?.endpoint ?? "https://api.example.com"}
            disabled={submitting}
            className="mt-1"
          />
        </div>
      )}

      <div>
        <Label htmlFor="qa-name" className="text-xs">
          Name <span className="text-muted-foreground">(auto: {defaultName || "—"})</span>
        </Label>
        <Input
          id="qa-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={defaultName}
          disabled={submitting}
          className="mt-1"
        />
      </div>

      {error && <p className="text-sm text-critical">{error}</p>}

      {(probeResult || probeError) && (
        <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs">
          {probeResult ? (
            probeResult.ok ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-emerald-600 dark:text-emerald-400">
                  <Check className="h-3 w-3" /> test passed
                </span>
                <span className="text-muted-foreground">
                  {probeResult.models.length} model{probeResult.models.length === 1 ? "" : "s"}
                  {probeResult.latency_ms != null ? ` · ${probeResult.latency_ms} ms` : ""}
                  {probeResult.checked_url ? ` · ${probeResult.checked_url}` : ""}
                </span>
              </div>
            ) : (
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-amber-600 dark:text-amber-400">
                    <Wifi className="h-3 w-3" />
                    {probeResult.reachable ? "reachable but rejected" : "unreachable"}
                  </span>
                  {probeResult.status_code != null && (
                    <span className="text-muted-foreground">HTTP {probeResult.status_code}</span>
                  )}
                </div>
                {probeResult.error && <p className="text-critical">{probeResult.error}</p>}
              </div>
            )
          ) : null}
          {probeError && <p className="text-critical">{probeError}</p>}
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button
          type="button"
          onClick={onSubmit}
          disabled={
            submitting ||
            !effectiveProvider ||
            (apiKeyRequired && !effectiveApiKey) ||
            !multiCredComplete ||
            (endpointRequired && !endpoint.trim())
          }
        >
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          {submitting ? "Adding…" : "Add key"}
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={onTest}
          disabled={!canTest}
        >
          {probing ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Wifi className="mr-1.5 h-3.5 w-3.5" />
          )}
          {probing ? "Testing…" : "Test"}
        </Button>
      </div>
    </div>
  );
}
