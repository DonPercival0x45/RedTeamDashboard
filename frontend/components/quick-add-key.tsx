"use client";

// Quick-add — one paste field for the API key, dropdowns for provider and
// kind, name auto-derives from `<provider> · <last4>`. Lives next to the
// JSON bulk importer; aimed at the "I copied a key from the console, drop
// it in" flow.

import { useMemo, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createProviderKey } from "@/lib/api";
import type { ProviderKey } from "@/lib/types";

type Kind = "model_provider" | "mcp_server" | "other";

interface Preset {
  slug: string; // sent to backend as `provider`
  label: string; // shown in the dropdown
  isLocal: boolean;
  endpoint?: string; // pre-fills the endpoint field
  modelsDefault?: string[];
  // Whether the endpoint field is REQUIRED for this provider.
  endpointRequired?: boolean;
}

// Default list — top cloud LLM providers as of 2026. Operators can also
// pick `Custom…` to type a slug for anything not on the list (the backend
// accepts any free-form provider string).
const PROVIDER_PRESETS: Preset[] = [
  {
    slug: "anthropic",
    label: "Anthropic",
    isLocal: false,
    modelsDefault: ["claude-opus-4-7", "claude-sonnet-4-6"],
  },
  {
    slug: "openai",
    label: "OpenAI",
    isLocal: false,
    modelsDefault: ["gpt-4o", "gpt-4o-mini"],
  },
  {
    slug: "google",
    label: "Google (Gemini)",
    isLocal: false,
    modelsDefault: ["gemini-2.0-pro", "gemini-2.0-flash"],
  },
  {
    slug: "azure",
    label: "Azure OpenAI",
    isLocal: false,
    endpointRequired: true,
    endpoint: "https://<resource>.openai.azure.com",
  },
  {
    slug: "xai",
    label: "xAI (Grok)",
    isLocal: false,
    modelsDefault: ["grok-3"],
  },
  {
    slug: "mistral",
    label: "Mistral",
    isLocal: false,
    modelsDefault: ["mistral-large-latest"],
  },
  {
    slug: "cohere",
    label: "Cohere",
    isLocal: false,
    modelsDefault: ["command-r-plus"],
  },
  {
    slug: "together",
    label: "Together AI",
    isLocal: false,
  },
  {
    slug: "groq",
    label: "Groq",
    isLocal: false,
    modelsDefault: ["llama-3.3-70b-versatile"],
  },
  {
    slug: "deepseek",
    label: "DeepSeek",
    isLocal: false,
    modelsDefault: ["deepseek-chat"],
  },
  {
    slug: "ollama",
    label: "Ollama (local)",
    isLocal: true,
    endpoint: "http://localhost:11434",
    modelsDefault: ["llama3.1:8b"],
  },
];

const CUSTOM_VALUE = "__custom__";

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
  const [endpoint, setEndpoint] = useState("");
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const preset = useMemo(
    () => PROVIDER_PRESETS.find((p) => p.slug === presetSlug),
    [presetSlug],
  );
  const isCustom = presetSlug === CUSTOM_VALUE;
  const isMcp = kind === "mcp_server";
  const effectiveProvider = isCustom
    ? customProvider.trim().toLowerCase()
    : presetSlug;
  // MCP server entries always need an endpoint; otherwise it's preset-driven.
  const endpointRequired = isMcp || preset?.endpointRequired === true;
  const showEndpoint = endpointRequired || isCustom || preset?.endpoint;

  const apiKeyRequired = !preset?.isLocal || isCustom || isMcp;

  const defaultName = useMemo(() => {
    if (!effectiveProvider) return "";
    const tail = apiKey.length >= 4 ? apiKey.slice(-4) : "";
    return tail
      ? `${effectiveProvider} · …${tail}`
      : `${effectiveProvider}`;
  }, [effectiveProvider, apiKey]);

  const onSubmit = async () => {
    setError(null);
    if (!effectiveProvider) {
      setError("Provider is required.");
      return;
    }
    if (apiKeyRequired && !apiKey) {
      setError("API key is required for this provider.");
      return;
    }
    if (endpointRequired && !endpoint.trim()) {
      setError("Endpoint is required for this provider/kind.");
      return;
    }
    setSubmitting(true);
    try {
      const created = await createProviderKey({
        name: (name.trim() || defaultName).trim(),
        provider: effectiveProvider,
        kind,
        is_local: preset?.isLocal ?? false,
        models: preset?.modelsDefault ?? [],
        endpoint: endpoint.trim() || null,
        api_key: apiKey || null,
      });
      onCreated(created);
      setApiKey("");
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
            }}
            disabled={submitting}
            className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          >
            {PROVIDER_PRESETS.map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.label}
              </option>
            ))}
            <option value={CUSTOM_VALUE}>Custom…</option>
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
      </div>

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

      <div className="flex items-center justify-end">
        <Button
          type="button"
          onClick={onSubmit}
          disabled={
            submitting ||
            !effectiveProvider ||
            (apiKeyRequired && !apiKey) ||
            (endpointRequired && !endpoint.trim())
          }
        >
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          {submitting ? "Adding…" : "Add key"}
        </Button>
      </div>
    </div>
  );
}
