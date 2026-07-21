// Single source of truth for the LLM-provider catalog used by both
// /settings/keys (QuickAddKey) and the engagement Scope tab (RunPrompt).
// Lifted out of quick-add-key.tsx 2026-06-30 so adding a new provider
// is a one-file edit and both surfaces stay in sync.

export interface ProviderPreset {
  /** The slug sent to the backend as `provider`. */
  slug: string;
  /** Display label in dropdowns. */
  label: string;
  /** True for providers running on the analyst's own infra (no API key
   *  required by default — Ollama). */
  isLocal: boolean;
  /** Pre-fills the endpoint field on Quick Add for providers that
   *  require it (Azure) or default to a known host (Ollama). */
  endpoint?: string;
  /** Whether the endpoint is mandatory for this provider. */
  endpointRequired?: boolean;
  /** Default model names. Shown in the RunPrompt dropdown's "Defaults"
   *  optgroup; offered as presets in the Keys-tab Quick Add form. */
  modelsDefault?: string[];
  /** What this preset is FOR. Defaults to model_provider; tool_secret
   *  entries are built-in tools that need a BYO API key (freeipapi,
   *  ipinfo, wigle). Only Quick Add / the key list see tool_secret
   *  presets — LLM surfaces (RunPrompt, default-model-card) filter
   *  them out. */
  kind?: "model_provider" | "tool_secret";
  /** Optional per-preset hint rendered under the API-key field in
   *  Quick Add. Used by wigle to explain the 2-credential JSON blob. */
  keyHint?: string;
  /** Where to send analysts to obtain the credentials. */
  signupUrl?: string;
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
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
    modelsDefault: ["meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"],
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
  {
    slug: "custom",
    label: "Custom (OpenAI-compatible)",
    isLocal: false,
    endpointRequired: true,
  },
  // Tool-secret providers (v2.20 → v2.24). Not LLMs — these are BYO API
  // keys for built-in enrichment tools. Only the Keys quick-add surface
  // renders them; LLM dropdowns filter by kind.
  {
    slug: "freeipapi",
    label: "freeipapi (IP geo)",
    isLocal: false,
    kind: "tool_secret",
    keyHint: "Paste the token string alone — the tool adds it as Authorization: Bearer.",
    signupUrl: "https://freeipapi.com",
  },
  {
    slug: "ipinfo",
    label: "ipinfo (ASN + hosting)",
    isLocal: false,
    kind: "tool_secret",
    keyHint: "Paste the token string alone — the tool appends ?token=<key>.",
    signupUrl: "https://ipinfo.io/account/token",
  },
  {
    slug: "wigle",
    label: "WiGLE (wifi networks)",
    isLocal: false,
    kind: "tool_secret",
    keyHint:
      'Paste as JSON: {"name":"AID...","token":"..."} — WiGLE needs both credentials.',
    signupUrl: "https://wigle.net/account",
  },
];

/** Sentinel value used by both dropdowns when the analyst picks
 *  "Custom..." and types in their own slug/model. */
export const CUSTOM_VALUE = "__custom__";

export function getPreset(slug: string): ProviderPreset | undefined {
  return PROVIDER_PRESETS.find((p) => p.slug === slug);
}

export function getPresetModels(slug: string): string[] {
  return getPreset(slug)?.modelsDefault ?? [];
}

export function getProviderLabel(slug: string): string {
  return getPreset(slug)?.label ?? slug;
}
