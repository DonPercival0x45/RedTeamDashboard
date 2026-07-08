"use client";

// v1.4.11: per-analyst default model (roadmap #3 / #12). The analyst
// picks a provider + model once on the Keys settings page; the
// Start-a-run prompt pre-selects it on every engagement instead of
// resetting to the hardcoded Anthropic default each run.

import { useEffect, useState } from "react";
import { Check } from "lucide-react";
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
import { useMe, useUpdateMyPreferencesMutation } from "@/lib/hooks";
import { PROVIDER_PRESETS } from "@/lib/llm-providers";

export function DefaultModelCard() {
  const { data: me } = useMe();
  const mutation = useUpdateMyPreferencesMutation();

  const initialProvider = me?.default_llm_provider ?? "";
  const initialModel = me?.default_llm_model ?? "";

  const [provider, setProvider] = useState(initialProvider);
  const [model, setModel] = useState(initialModel);
  const [error, setError] = useState<string | null>(null);

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
            {PROVIDER_PRESETS.map((p) => (
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
          <Input
            id="dm-model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="e.g. gpt-4o, claude-opus-4-7, llama3.1:8b"
            className="font-mono text-sm"
          />
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
