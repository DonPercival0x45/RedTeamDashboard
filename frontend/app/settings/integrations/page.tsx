"use client";

// v0.9.0: Integrations tab. The generic 3rd-party-app hub of the
// dashboard — home for everything that isn't an API key. Provider
// catalog at the top (Discord / Teams / GitHub / Custom tiles), each
// click pops a centered modal with step-by-step setup + config fields.
// Saved integrations render as their own tiles below, with edit + delete
// affordances. Admin-only.

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { IntegrationSetupModal } from "@/components/integration-setup-modal";
import {
  qk,
  useDeleteIntegrationMutation,
  useIntegrations,
  useMe,
} from "@/lib/hooks";
import {
  CUSTOM_PROVIDER,
  PROVIDER_CATALOG,
  PURPOSE_LABELS,
  findProvider,
  type ProviderDef,
} from "@/lib/integrations-catalog";
import { cn } from "@/lib/utils";
import type { Integration } from "@/lib/types";

type ModalMode =
  | { kind: "new"; provider: ProviderDef }
  | { kind: "edit"; provider: ProviderDef; row: Integration }
  | null;

export default function SettingsIntegrationsPage() {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const { data: rows, error: queryError } = useIntegrations();
  const deleteMutation = useDeleteIntegrationMutation();
  const [modal, setModal] = useState<ModalMode>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const error =
    localError ??
    (queryError instanceof Error
      ? queryError.message
      : queryError
        ? String(queryError)
        : null);

  const onDelete = async (row: Integration) => {
    if (
      !window.confirm(
        `Remove the "${row.name}" integration? This cannot be undone.`,
      )
    )
      return;
    setLocalError(null);
    try {
      await deleteMutation.mutateAsync(row.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
    }
  };

  const configured = useMemo(() => rows ?? [], [rows]);

  // Non-admin guard. The endpoints are admin-gated server-side; this
  // surfaces a clean message instead of a sea of 403s.
  if (me && !me.is_admin) {
    return (
      <div className="mx-auto max-w-4xl space-y-6 px-4 py-6">
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← engagements
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Integrations</h1>
        <Card>
          <CardContent className="py-4 text-sm text-muted-foreground">
            Integrations are admin-only. Ask an admin to configure third-party
            services (Discord, Teams, GitHub push, custom webhooks).
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6">
      <div>
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← engagements
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          Integrations
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Wire the dashboard up to third-party services — outbound webhooks,
          inbound bots, push targets. Anything that isn&apos;t an API key
          credential lives here. (API keys belong on{" "}
          <Link
            href="/settings/keys"
            className="underline decoration-dotted hover:decoration-solid"
          >
            /settings/keys
          </Link>
          .)
        </p>
      </div>

      {error && <p className="text-sm text-critical">{error}</p>}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Add an integration</CardTitle>
          <CardDescription>
            Pick a provider. The setup modal walks you through whatever the
            provider needs.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {PROVIDER_CATALOG.map((p) => (
              <CatalogTile
                key={p.type}
                provider={p}
                onClick={() => setModal({ kind: "new", provider: p })}
              />
            ))}
            <CustomCatalogTile
              onClick={() =>
                setModal({ kind: "new", provider: CUSTOM_PROVIDER })
              }
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Configured</CardTitle>
          <CardDescription>
            Click any tile to edit. Send-event paths
            (status_notifier, feedback push, ROADMAP push) match on{" "}
            <strong>purpose</strong>, so a row with no purpose set is just
            sitting idle.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {rows === null ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : configured.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No integrations configured yet. Pick one from the catalog above.
            </p>
          ) : (
            <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {configured.map((row) => {
                const provider =
                  findProvider(row.type) ?? CUSTOM_PROVIDER;
                return (
                  <li key={row.id}>
                    <ConfiguredTile
                      row={row}
                      provider={provider}
                      onEdit={() =>
                        setModal({ kind: "edit", provider, row })
                      }
                      onDelete={() => onDelete(row)}
                    />
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {modal && (
        <IntegrationSetupModal
          provider={modal.provider}
          existing={modal.kind === "edit" ? modal.row : null}
          onClose={() => setModal(null)}
          onSaved={() => {
            setModal(null);
            void qc.invalidateQueries({ queryKey: qk.integrations() });
          }}
        />
      )}
    </div>
  );
}

function CatalogTile({
  provider,
  onClick,
}: {
  provider: ProviderDef;
  onClick: () => void;
}) {
  const Icon = provider.icon;
  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex aspect-square flex-col items-center justify-center gap-2 rounded-lg border border-border bg-card/40 p-4 text-center transition-colors hover:border-foreground/40 hover:bg-secondary/40"
    >
      <Icon className="h-10 w-10 text-foreground/70 transition-colors group-hover:text-foreground" />
      <p className="text-sm font-medium text-foreground">{provider.label}</p>
      <p className="line-clamp-2 text-[10px] text-muted-foreground">
        {provider.tagline}
      </p>
    </button>
  );
}

function CustomCatalogTile({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex aspect-square flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-card/30 p-4 text-center transition-colors hover:border-foreground/40 hover:bg-secondary/40"
    >
      <div className="flex h-10 w-10 items-center justify-center rounded-full border border-dashed border-foreground/40">
        <Plus className="h-5 w-5 text-foreground/70 transition-colors group-hover:text-foreground" />
      </div>
      <p className="text-sm font-medium text-foreground">Custom</p>
      <p className="line-clamp-2 text-[10px] text-muted-foreground">
        Any webhook + JSON template. Upload your own logo.
      </p>
    </button>
  );
}

function ConfiguredTile({
  row,
  provider,
  onEdit,
  onDelete,
}: {
  row: Integration;
  provider: ProviderDef;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const Icon = provider.icon;
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-lg border bg-card/40 p-3 transition-colors",
        row.enabled
          ? "border-emerald-500/40"
          : "border-border",
      )}
    >
      <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-md border border-border bg-background">
        {row.logo_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={row.logo_url}
            alt={row.name}
            className="h-full w-full rounded-md object-cover"
          />
        ) : (
          <Icon className="h-6 w-6 text-foreground/70" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={onEdit}
          className="w-full text-left"
        >
          <p className="truncate text-sm font-medium text-foreground">
            {row.display_name || row.name}
          </p>
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {provider.label} · {PURPOSE_LABELS[row.purpose]}
          </p>
        </button>
        <p
          className={cn(
            "mt-1 text-[10px]",
            row.enabled ? "text-emerald-300" : "text-muted-foreground",
          )}
        >
          {row.enabled ? "Enabled" : "Disabled"} · saved{" "}
          {new Date(row.updated_at).toLocaleString()}
        </p>
      </div>
      <div className="flex shrink-0 flex-col gap-1">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onEdit}
          className="h-7 w-7 p-0"
          aria-label="Edit"
        >
          <Pencil className="h-3.5 w-3.5" />
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onDelete}
          className="h-7 w-7 p-0 text-muted-foreground hover:text-critical"
          aria-label="Delete"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}
