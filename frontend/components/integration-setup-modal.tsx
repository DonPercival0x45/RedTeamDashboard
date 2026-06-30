"use client";

// v0.9.0: centered setup modal for a provider on /settings/integrations.
// Renders the provider's step-by-step instructions at the top, then a
// form with the provider's declared config fields, then Save / Cancel.
// In `edit` mode the form is pre-filled with the row's current config
// (secrets come back masked — the modal sends the mask string back to
// signal "keep the stored value").

import { useCallback, useEffect, useMemo, useState } from "react";
import { Save, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { createIntegration, updateIntegration } from "@/lib/api";
import {
  PURPOSE_LABELS,
  type ProviderDef,
  type ProviderField,
} from "@/lib/integrations-catalog";
import type {
  Integration,
  IntegrationCreate,
  IntegrationPurpose,
  IntegrationUpdate,
} from "@/lib/types";

const PURPOSES: IntegrationPurpose[] = [
  "feedback",
  "status_alerts",
  "roadmap_push",
  "manual",
];

const MAX_LOGO_BYTES = 64 * 1024; // 64KB — keep data URLs small.

export function IntegrationSetupModal({
  provider,
  existing,
  onClose,
  onSaved,
}: {
  provider: ProviderDef;
  existing: Integration | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = existing !== null;

  const [name, setName] = useState(
    existing?.name ?? `${provider.label} integration`,
  );
  const [purpose, setPurpose] = useState<IntegrationPurpose>(
    existing?.purpose ?? provider.defaultPurpose,
  );
  const [enabled, setEnabled] = useState(existing?.enabled ?? false);
  const [config, setConfig] = useState<Record<string, string>>(() => {
    const cfg = (existing?.config ?? {}) as Record<string, unknown>;
    const out: Record<string, string> = {};
    for (const field of provider.fields) {
      const v = cfg[field.key];
      out[field.key] = typeof v === "string" ? v : "";
    }
    return out;
  });
  const [logoUrl, setLogoUrl] = useState<string | null>(existing?.logo_url ?? null);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const requiredMissing = useMemo(() => {
    return provider.fields
      .filter((f) => f.required)
      .filter((f) => {
        const v = config[f.key];
        // For secrets in edit mode, the masked placeholder still counts
        // as "configured" — the backend keeps the stored value.
        if (
          f.kind === "secret" &&
          isEdit &&
          typeof v === "string" &&
          v.startsWith("…")
        ) {
          return false;
        }
        return !v || !v.trim();
      })
      .map((f) => f.label);
  }, [provider.fields, config, isEdit]);

  const onLogoChange = useCallback(async (file: File) => {
    setLogoError(null);
    if (file.size > MAX_LOGO_BYTES) {
      setLogoError(
        `Logo too large (${Math.ceil(file.size / 1024)}KB). Max is 64KB.`,
      );
      return;
    }
    if (!file.type.startsWith("image/")) {
      setLogoError("Logo must be a PNG, SVG, or JPEG image.");
      return;
    }
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(new Error("Failed to read logo file"));
      reader.readAsDataURL(file);
    });
    setLogoUrl(dataUrl);
  }, []);

  const onSubmit = useCallback(async () => {
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (requiredMissing.length > 0) {
      setError(`Missing required field(s): ${requiredMissing.join(", ")}`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // Trim every config value; preserve masked values as-is so the
      // backend's _merge_config keeps the stored secret.
      const submittedConfig: Record<string, string> = {};
      for (const [k, v] of Object.entries(config)) {
        if (v == null) continue;
        submittedConfig[k] = v.trim();
      }
      if (isEdit && existing) {
        const body: IntegrationUpdate = {
          name: name.trim(),
          purpose,
          enabled,
          config: submittedConfig,
          logo_url: provider.acceptsCustomLogo ? logoUrl : null,
        };
        await updateIntegration(existing.id, body);
      } else {
        const body: IntegrationCreate = {
          type: provider.type,
          name: name.trim(),
          purpose,
          enabled,
          config: submittedConfig,
          logo_url: provider.acceptsCustomLogo ? logoUrl : null,
        };
        await createIntegration(body);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [
    config,
    enabled,
    existing,
    isEdit,
    logoUrl,
    name,
    onSaved,
    provider,
    purpose,
    requiredMissing,
  ]);

  // Lock background scroll while the modal is open.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  return (
    <>
      <div
        className="fixed inset-0 z-[60] bg-black/70"
        onClick={busy ? undefined : onClose}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${isEdit ? "Edit" : "Add"} ${provider.label} integration`}
        className="fixed left-1/2 top-1/2 z-[70] flex max-h-[90vh] w-[min(720px,94vw)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-lg border border-border bg-popover shadow-xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-border p-5">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-border bg-background">
              {logoUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={logoUrl}
                  alt={provider.label}
                  className="h-full w-full rounded-md object-cover"
                />
              ) : (
                <provider.icon className="h-5 w-5 text-foreground/70" />
              )}
            </div>
            <div className="min-w-0">
              <h3 className="truncate text-sm font-semibold text-foreground">
                {isEdit ? "Edit" : "Add"} {provider.label}
              </h3>
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                {provider.tagline}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={busy ? undefined : onClose}
            disabled={busy}
            className="text-muted-foreground hover:text-foreground disabled:opacity-50"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto p-5">
          {provider.setupSteps.length > 0 && (
            <div className="rounded-md border border-border bg-secondary/30 p-3">
              <p className="mb-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                Setup
              </p>
              <ol className="ml-4 list-decimal space-y-1.5 text-xs text-muted-foreground">
                {provider.setupSteps.map((step, i) => (
                  <li key={i}>{step}</li>
                ))}
              </ol>
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="ig-name" className="text-xs">
                Name
              </Label>
              <Input
                id="ig-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Feedback channel"
                disabled={busy}
                className="mt-1"
              />
            </div>
            <div>
              <Label htmlFor="ig-purpose" className="text-xs">
                Purpose
              </Label>
              <select
                id="ig-purpose"
                value={purpose}
                onChange={(e) =>
                  setPurpose(e.target.value as IntegrationPurpose)
                }
                disabled={busy}
                className="mt-1 flex h-10 w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                {PURPOSES.map((p) => (
                  <option key={p} value={p}>
                    {PURPOSE_LABELS[p]}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {provider.fields.map((field) => (
            <ConfigField
              key={field.key}
              field={field}
              value={config[field.key] ?? ""}
              onChange={(v) =>
                setConfig((prev) => ({ ...prev, [field.key]: v }))
              }
              disabled={busy}
            />
          ))}

          {provider.acceptsCustomLogo && (
            <div>
              <Label className="text-xs">Logo (square, max 64KB)</Label>
              <div className="mt-1 flex items-center gap-3">
                <label
                  className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border bg-secondary px-3 py-1.5 text-xs hover:bg-secondary/80"
                >
                  <Upload className="h-3.5 w-3.5" />
                  Choose image
                  <input
                    type="file"
                    accept="image/png,image/svg+xml,image/jpeg"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) void onLogoChange(file);
                    }}
                    disabled={busy}
                  />
                </label>
                {logoUrl && (
                  <>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={logoUrl}
                      alt="Logo preview"
                      className="h-10 w-10 rounded-md border border-border object-cover"
                    />
                    <button
                      type="button"
                      onClick={() => setLogoUrl(null)}
                      disabled={busy}
                      className="text-xs text-muted-foreground hover:text-critical"
                    >
                      Remove
                    </button>
                  </>
                )}
              </div>
              {logoError && (
                <p className="mt-1 text-xs text-critical">{logoError}</p>
              )}
            </div>
          )}

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              disabled={busy}
              className="h-4 w-4 rounded border-border"
            />
            <span>Enabled — when off, this integration never fires.</span>
          </label>

          {error && <p className="text-sm text-critical">{error}</p>}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-border p-4">
          <p className="text-[10px] text-muted-foreground">
            {isEdit ? `Editing ${existing?.id.slice(0, 8)}` : "New integration"}
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={onClose}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button type="button" onClick={onSubmit} disabled={busy}>
              <Save className="mr-1.5 h-3.5 w-3.5" />
              {busy ? "Saving…" : isEdit ? "Save changes" : "Add integration"}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

function ConfigField({
  field,
  value,
  onChange,
  disabled,
}: {
  field: ProviderField;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
}) {
  return (
    <div>
      <Label htmlFor={`cfg-${field.key}`} className="text-xs">
        {field.label}
        {field.required && <span className="text-critical"> *</span>}
      </Label>
      {field.kind === "json" ? (
        <Textarea
          id={`cfg-${field.key}`}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          rows={3}
          disabled={disabled}
          className="mt-1 font-mono text-xs"
        />
      ) : (
        <Input
          id={`cfg-${field.key}`}
          type={field.kind === "secret" ? "password" : "text"}
          autoComplete="off"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          disabled={disabled}
          className={
            field.kind === "url" || field.kind === "secret"
              ? "mt-1 font-mono"
              : "mt-1"
          }
        />
      )}
      {field.help && (
        <p className="mt-1 text-[10px] text-muted-foreground">{field.help}</p>
      )}
    </div>
  );
}
