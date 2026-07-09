"use client";

import Link from "next/link";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ProviderKeyImporter } from "@/components/provider-key-importer";
import { ProviderKeyList } from "@/components/provider-key-list";
import { QuickAddKey } from "@/components/quick-add-key";
import { DefaultModelCard } from "@/components/default-model-card";
import { qk, useMe, useProviderKeys } from "@/lib/hooks";
import type { ProviderKeyImportResult } from "@/lib/types";

// Per-user BYO key vault. Analysts upload a JSON list of credentials the
// system encrypts and stores; only the masked tail comes back over the wire.
// Phase 7+: keys are scoped to the acting analyst's Entra identity.

export default function SettingsKeysPage() {
  // v1.0.0: react-query owns both queries. Guest role short-circuits the
  // keys query via `enabled`, so we don't fire it for a role that can't
  // list.
  const qc = useQueryClient();
  const { data: me } = useMe();
  const keysQuery = useProviderKeys();
  const keys = me?.role === "guest" ? null : keysQuery.data ?? null;
  const error =
    me?.role === "guest"
      ? null
      : keysQuery.error instanceof Error
        ? keysQuery.error.message
        : keysQuery.error
          ? String(keysQuery.error)
          : null;
  const [lastImport, setLastImport] =
    useState<ProviderKeyImportResult | null>(null);
  const reload = async () => {
    await qc.invalidateQueries({ queryKey: qk.providerKeys() });
  };

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
          Models
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          API credentials this tool uses on your behalf. Keys never leave
          this browser as plaintext after upload — only a 4-char tail is
          shown back. Rotate by uploading again with the same name (after
          deleting the old one).
        </p>
        <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-200">
          <span className="font-semibold uppercase tracking-wide">Session-only.</span>{" "}
          v1.25.0: keys are held for the entire session — no more 30-min
          re-uploads. They&apos;re cleared only when you delete them here
          or the backend restarts. Only you can use your own keys; no
          other analyst can see them.
        </div>
      </div>

      {me?.role === "guest" && (
        <Card>
          <CardContent className="py-6 text-sm text-muted-foreground">
            Your role is <strong className="text-foreground">guest</strong>{" "}
            — you can read engagements but can&apos;t upload, list, or use BYO
            provider keys. Ask an admin to upgrade you to{" "}
            <strong className="text-foreground">user</strong> in the
            Management tab.
          </CardContent>
        </Card>
      )}

      {me?.role !== "guest" && (
      <>
      <DefaultModelCard />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Quick add</CardTitle>
          <CardDescription>
            Drop in one API key — pick the provider, paste, save. Use this
            for the common case; the bulk JSON importer below is still here
            for multi-key uploads.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <QuickAddKey onCreated={async () => { await reload(); }} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Bulk import (JSON)</CardTitle>
          <CardDescription>
            Paste or upload a JSON list of credentials — useful when you
            have several keys to migrate at once.
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
      </>
      )}
    </div>
  );
}
