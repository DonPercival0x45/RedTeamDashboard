"use client";

import { useCallback, useRef, useState } from "react";
import { Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { importProviderKeys } from "@/lib/api";
import type {
  ProviderKeyImportPayload,
  ProviderKeyImportResult,
} from "@/lib/types";

const SAMPLE = `{
  "providers": [
    {
      "name": "My Anthropic",
      "provider": "anthropic",
      "kind": "model_provider",
      "models": ["claude-opus-4-7", "claude-sonnet-4-6"],
      "api_key": "sk-ant-…"
    },
    {
      "name": "Local Ollama",
      "provider": "ollama",
      "kind": "model_provider",
      "is_local": true,
      "endpoint": "http://localhost:11434",
      "models": ["llama3.1:8b"]
    },
    {
      "name": "GitHub MCP",
      "provider": "github",
      "kind": "mcp_server",
      "endpoint": "https://api.github.com",
      "api_key": "ghp_…"
    }
  ]
}`;

// Parses + posts a JSON blob of provider entries to /me/provider-keys/import.
// The parser is just JSON.parse here — the server is the source of truth for
// schema validation, so a malformed entry surfaces back as a 422 from the API.

export function ProviderKeyImporter({
  onImported,
}: {
  onImported: (result: ProviderKeyImportResult) => void;
}) {
  const [text, setText] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const validate = (raw: string): ProviderKeyImportPayload | null => {
    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") {
        setParseError("Top-level value must be an object.");
        return null;
      }
      const list = Array.isArray(parsed)
        ? parsed
        : Array.isArray(parsed.providers)
          ? parsed.providers
          : null;
      if (!list) {
        setParseError(
          "Expected either an array of entries or { providers: [...] }.",
        );
        return null;
      }
      setParseError(null);
      return { providers: list };
    } catch (err) {
      setParseError(err instanceof Error ? err.message : String(err));
      return null;
    }
  };

  const onFileChosen = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) return;
      const body = await file.text();
      setText(body);
      event.target.value = "";
    },
    [],
  );

  const submit = async () => {
    const payload = validate(text);
    if (!payload) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await importProviderKeys(payload);
      onImported(result);
      setText("");
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-3 rounded-md border border-dashed border-border p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium">Bulk import</p>
          <p className="text-xs text-muted-foreground">
            Upload a .json file or paste the JSON below. Entries with{" "}
            <code className="font-mono">is_local: true</code> omit{" "}
            <code className="font-mono">api_key</code>;{" "}
            <code className="font-mono">mcp_server</code> entries require an{" "}
            <code className="font-mono">endpoint</code>.
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => fileRef.current?.click()}
        >
          <Upload className="mr-1.5 h-3.5 w-3.5" />
          File
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          className="hidden"
          onChange={onFileChosen}
        />
      </div>

      <Textarea
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setParseError(null);
        }}
        rows={10}
        placeholder={SAMPLE}
        className="font-mono text-xs"
      />

      {parseError && (
        <p className="text-xs text-critical">JSON error: {parseError}</p>
      )}
      {submitError && (
        <p className="text-xs text-critical">{submitError}</p>
      )}

      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={submitting || !text.trim()}
          onClick={submit}
        >
          {submitting ? "Importing…" : "Import"}
        </Button>
      </div>
    </div>
  );
}
