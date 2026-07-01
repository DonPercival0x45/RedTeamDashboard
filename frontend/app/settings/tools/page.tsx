"use client";

// v0.11.0: Tools tab. The admin-only catalog + upload flow. Each tool
// row lands as draft after upload; static validation (AST allow-list +
// manifest schema) surfaces inline. Admin explicitly approves.
// Invocation runtime lands in v0.12.0 — v0.11 lists tools you can't
// run yet.

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, Trash2, Wrench, X } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ToolUploader } from "@/components/tool-uploader";
import {
  approveTool,
  deleteTool,
  getMe,
  listTools,
  revokeTool,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Me, ToolRead } from "@/lib/types";

const STATUS_TONE: Record<ToolRead["status"], string> = {
  draft: "border-amber-500/50 bg-amber-500/10 text-amber-200",
  approved: "border-emerald-500/50 bg-emerald-500/10 text-emerald-200",
  revoked: "border-rose-500/50 bg-rose-500/10 text-rose-200",
};

const KIND_LABEL: Record<ToolRead["kind"], string> = {
  python: "Python",
  shell: "Shell",
  binary: "Binary",
};

const LANE_LABEL: Record<ToolRead["lane"], string> = {
  analyst: "Analyst lane",
  admin: "Admin lane",
};

export default function SettingsToolsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [tools, setTools] = useState<ToolRead[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploaderOpen, setUploaderOpen] = useState(false);
  const [inspecting, setInspecting] = useState<ToolRead | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      setTools(await listTools());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void getMe().then(setMe).catch(() => setMe(null));
    void reload();
  }, [reload]);

  const grouped = useMemo(() => {
    const t = tools ?? [];
    return {
      draft: t.filter((x) => x.status === "draft"),
      approved: t.filter((x) => x.status === "approved"),
      revoked: t.filter((x) => x.status === "revoked"),
    };
  }, [tools]);

  if (me && !me.is_admin) {
    return (
      <div className="mx-auto max-w-4xl space-y-6 px-4 py-6">
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← engagements
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Tools</h1>
        <Card>
          <CardContent className="py-4 text-sm text-muted-foreground">
            Tools management is admin-only. Ask an admin to register new
            tools; you&apos;ll be able to invoke approved ones from the
            engagement view once v0.12.0 lands.
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
        <h1 className="mt-2 flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Wrench className="h-6 w-6" />
          Tools
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          First-party Python tools and admin-curated binaries. Every tool
          declares a YAML manifest; the analyst lane runs an AST allow-list
          before the tool lands in <strong>draft</strong>, and an admin
          promotes it to <strong>approved</strong> before an engagement can
          invoke it. Invocation runtime ships in v0.12.0.
        </p>
      </div>

      {error && (
        <Card className="border-critical/60">
          <CardContent className="py-3 text-sm text-critical">
            {error}
          </CardContent>
        </Card>
      )}

      <div className="flex items-center justify-between">
        <div className="text-xs text-muted-foreground">
          {tools ? `${tools.length} registered` : "Loading…"}
        </div>
        <Button
          size="sm"
          onClick={() => setUploaderOpen((v) => !v)}
        >
          <Plus className="mr-1.5 h-4 w-4" />
          {uploaderOpen ? "Close uploader" : "Register tool"}
        </Button>
      </div>

      {uploaderOpen && (
        <ToolUploader
          onDone={async () => {
            setUploaderOpen(false);
            await reload();
          }}
        />
      )}

      <ToolGroup
        title="Draft"
        subtitle="Uploaded but not yet approved. Admin decision required before an engagement can invoke."
        tools={grouped.draft}
        onInspect={setInspecting}
        onApprove={async (id, override) => {
          await approveTool(id, { overrideValidation: override });
          await reload();
        }}
        onDelete={async (id) => {
          if (!window.confirm("Delete this draft tool?")) return;
          await deleteTool(id);
          await reload();
        }}
      />

      <ToolGroup
        title="Approved"
        subtitle="Live catalog. Once v0.12 ships, engagements can invoke these."
        tools={grouped.approved}
        onInspect={setInspecting}
        onRevoke={async (id) => {
          if (
            !window.confirm(
              "Revoke this tool? Past invocations are preserved; new ones will be blocked.",
            )
          )
            return;
          await revokeTool(id);
          await reload();
        }}
      />

      <ToolGroup
        title="Revoked"
        subtitle="Preserved for audit. Re-upload as a new row to bring back."
        tools={grouped.revoked}
        onInspect={setInspecting}
        onDelete={async (id) => {
          if (!window.confirm("Hard-delete this revoked row?")) return;
          await deleteTool(id);
          await reload();
        }}
      />

      {inspecting && (
        <ToolInspector tool={inspecting} onClose={() => setInspecting(null)} />
      )}
    </div>
  );
}

function ToolGroup({
  title,
  subtitle,
  tools,
  onInspect,
  onApprove,
  onRevoke,
  onDelete,
}: {
  title: string;
  subtitle: string;
  tools: ToolRead[];
  onInspect: (t: ToolRead) => void;
  onApprove?: (id: string, overrideValidation: boolean) => Promise<void>;
  onRevoke?: (id: string) => Promise<void>;
  onDelete?: (id: string) => Promise<void>;
}) {
  if (tools.length === 0) return null;
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{subtitle}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {tools.map((t) => (
          <ToolRow
            key={t.id}
            tool={t}
            onInspect={() => onInspect(t)}
            onApprove={onApprove}
            onRevoke={onRevoke}
            onDelete={onDelete}
          />
        ))}
      </CardContent>
    </Card>
  );
}

function ToolRow({
  tool,
  onInspect,
  onApprove,
  onRevoke,
  onDelete,
}: {
  tool: ToolRead;
  onInspect: () => void;
  onApprove?: (id: string, overrideValidation: boolean) => Promise<void>;
  onRevoke?: (id: string) => Promise<void>;
  onDelete?: (id: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const validationOk = validationClean(tool);

  const wrap = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border/60 bg-background px-3 py-2.5">
      <button
        type="button"
        onClick={onInspect}
        className="flex-1 text-left"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{tool.name}</span>
          <span
            className={cn(
              "rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
              STATUS_TONE[tool.status],
            )}
          >
            {tool.status}
          </span>
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {KIND_LABEL[tool.kind]} · {LANE_LABEL[tool.lane]} · {tool.task_kind} · {tool.risk_level}
          </span>
        </div>
        {tool.description && (
          <p className="mt-0.5 text-xs text-muted-foreground">
            {tool.description}
          </p>
        )}
      </button>
      <div className="flex items-center gap-2">
        {onApprove && (
          <Button
            size="sm"
            disabled={busy}
            onClick={() =>
              void wrap(() => onApprove(tool.id, !validationOk))
            }
            title={
              validationOk
                ? "Approve this tool"
                : "Static validation flagged issues — approving records override in audit_log"
            }
            className={
              validationOk
                ? undefined
                : "border-amber-500/50 text-amber-200 hover:bg-amber-500/10"
            }
          >
            {validationOk ? "Approve" : "Approve (override)"}
          </Button>
        )}
        {onRevoke && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void wrap(() => onRevoke(tool.id))}
          >
            Revoke
          </Button>
        )}
        {onDelete && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void wrap(() => onDelete(tool.id))}
            className="border-rose-500/40 text-rose-200 hover:bg-rose-500/10"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}

function validationClean(tool: ToolRead): boolean {
  const ast = (tool.validation as Record<string, unknown>)?.ast as
    | { disallowed_imports?: string[]; banned_calls?: string[] }
    | undefined;
  if (!ast) return true;
  return (
    (ast.disallowed_imports?.length ?? 0) === 0 &&
    (ast.banned_calls?.length ?? 0) === 0
  );
}

function ToolInspector({
  tool,
  onClose,
}: {
  tool: ToolRead;
  onClose: () => void;
}) {
  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/60"
        onClick={onClose}
        aria-hidden
      />
      <aside className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col overflow-y-auto border-l border-border bg-popover p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs text-muted-foreground">
              {KIND_LABEL[tool.kind]} · {LANE_LABEL[tool.lane]} · v{tool.version}
            </div>
            <h2 className="mt-1 text-lg font-semibold leading-tight">
              {tool.name}
            </h2>
            {tool.description && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                {tool.description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <section className="mt-5">
          <h3 className="text-sm font-medium">Manifest</h3>
          <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-[11px] text-muted-foreground">
            {JSON.stringify(tool.manifest, null, 2)}
          </pre>
        </section>

        <section className="mt-5">
          <h3 className="text-sm font-medium">Validation</h3>
          <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-[11px] text-muted-foreground">
            {JSON.stringify(tool.validation, null, 2)}
          </pre>
        </section>

        <section className="mt-5 text-xs text-muted-foreground">
          <p>
            Created {new Date(tool.created_at).toLocaleString()}
            {tool.approved_at && (
              <> · Approved {new Date(tool.approved_at).toLocaleString()}</>
            )}
          </p>
        </section>
      </aside>
    </>
  );
}
