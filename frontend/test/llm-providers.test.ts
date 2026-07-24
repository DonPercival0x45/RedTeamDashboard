import { describe, expect, it } from "vitest";
import {
  CUSTOM_VALUE,
  PROVIDER_PRESETS,
  getPreset,
  getPresetModels,
  getProviderLabel,
} from "@/lib/llm-providers";

// The provider catalog is the single source of truth shared by the Keys
// quick-add and the engagement RunPrompt. A drift here means the dropdown
// offers a provider the backend rejects (or hides one it accepts), so the
// shape is locked by these tests.

describe("PROVIDER_PRESETS", () => {
  it("exposes every LLM provider the backend accepts", () => {
    const slugs = PROVIDER_PRESETS.filter((p) => p.kind !== "tool_secret")
      .map((p) => p.slug);
    // Must cover the provider union in lib/types + the CLI --provider set.
    for (const expected of [
      "anthropic",
      "openai",
      "google",
      "azure",
      "xai",
      "mistral",
      "cohere",
      "together",
      "groq",
      "deepseek",
      "moonshot",
      "ollama",
      "custom",
    ]) {
      expect(slugs, `missing LLM provider ${expected}`).toContain(expected);
    }
  });

  it("has unique slugs", () => {
    const slugs = PROVIDER_PRESETS.map((p) => p.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
  });

  it("marks ollama as the only local provider", () => {
    const local = PROVIDER_PRESETS.filter((p) => p.isLocal).map((p) => p.slug);
    expect(local).toEqual(["ollama"]);
  });

  it("tags tool-secret providers so LLM dropdowns can filter them out", () => {
    const secrets = PROVIDER_PRESETS.filter((p) => p.kind === "tool_secret");
    for (const s of secrets) {
      expect(s.kind).toBe("tool_secret");
    }
    // freeipapi/ipinfo/wigle are the built-in enrichment tools that need BYO keys.
    expect(secrets.map((s) => s.slug).sort()).toEqual([
      "freeipapi",
      "ipinfo",
      "wigle",
    ]);
  });
});

describe("getPreset / getPresetModels / getProviderLabel", () => {
  it("returns the matching preset or undefined", () => {
    expect(getPreset("openai")?.label).toBe("OpenAI");
    expect(getPreset("does-not-exist")).toBeUndefined();
  });

  it("returns preset models or empty array for unknown", () => {
    expect(getPresetModels("anthropic")).toContain("claude-opus-4-7");
    expect(getPresetModels("custom")).toEqual([]);
  });

  it("falls back to the slug for unknown provider labels", () => {
    expect(getProviderLabel("openai")).toBe("OpenAI");
    expect(getProviderLabel("mystery")).toBe("mystery");
  });
});

describe("CUSTOM_VALUE", () => {
  it("is a stable sentinel that never collides with a real slug", () => {
    expect(CUSTOM_VALUE).toBe("__custom__");
    expect(PROVIDER_PRESETS.map((p) => p.slug)).not.toContain(CUSTOM_VALUE);
  });
});
