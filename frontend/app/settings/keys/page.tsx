"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ProviderKeyImporter } from "@/components/provider-key-importer";
import { ProviderKeyList } from "@/components/provider-key-list";
import { listProviderKeys } from "@/lib/api";
import type { ProviderKey, ProviderKeyImportResult } from "@/lib/types";

// Per-user BYO key vault. Analysts upload a JSON list of credentials the
// system encrypts and stores; only the masked tail comes back over the wire.
// Phase 7+: keys are scoped to the acting analyst's Entra identity.

export default function SettingsKeysPage() {
  const [keys, setKeys] = useState<ProviderKey[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastImport, setLastImport] =
    useState<ProviderKeyImportResult | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      setKeys(await listProviderKeys());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
      <div>
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← engagements
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          Provider keys
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          API credentials this tool uses on your behalf. Keys are stored
          encrypted in the database and never returned over the wire after
          upload — only a 4-char tail is shown back. Rotate by uploading
          again with the same name (after deleting the old one).
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Upload</CardTitle>
          <CardDescription>
            Bring your own keys for any model provider (Anthropic, OpenAI,
            Azure, Ollama, …) or MCP server (GitHub, web search, …).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ProviderKeyImporter
            onImported={async (result) => {
              setLastImport(result);
              await reload();
            }}
          />
          {lastImport && (
            <p className="mt-3 text-xs text-muted-foreground">
              Imported {lastImport.created.length}
              {lastImport.duplicates.length > 0
                ? ` · ${lastImport.duplicates.length} duplicates skipped`
                : ""}
              {lastImport.errors.length > 0
                ? ` · ${lastImport.errors.length} errors`
                : ""}
              .
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Stored keys</CardTitle>
          <CardDescription>
            One row per provider entry. Deletion is immediate and
            unrecoverable — re-upload to replace.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {error && <p className="text-sm text-critical">{error}</p>}
          {keys === null && !error && (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
          {keys !== null && (
            <ProviderKeyList keys={keys} onChanged={reload} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
