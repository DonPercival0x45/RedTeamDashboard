"use client";

// v0.12.0: Three-tab tool uploader.
//
//   Auto-detect — analyst uploads a .py file with a top-of-file
//                 ``TOOL = {...}`` dict. Backend reads what it can via
//                 AST and returns a preview; missing required fields
//                 surface as follow-on prompts.
//   Guided form — every manifest field as an input. Analyst fills each
//                 explicitly; frontend renders it as YAML on submit.
//   YAML        — advanced fallback. The raw textarea from v0.11.
//
// All three eventually hit the same POST /tools endpoint with a YAML
// manifest + optional source file, so the server code path is one
// thing.

import { useCallback, useMemo, useState } from "react";
import { FileCode, FileText, Sparkles, Upload } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { inferToolManifest, uploadTool } from "@/lib/api";
import type { ToolInferResponse, ToolUploadResponse } from "@/lib/types";

const STARTER_YAML = `apiVersion: rtd.tools/v1
kind: Tool
metadata:
  name: subdomain-crt
  description: crt.sh subdomain enumeration via HTTPS
spec:
  kind: python
  lane: analyst
  entrypoint: main.py
  args:
    - name: target
      type: string
      required: true
      scope_kind: domain
  timeout_seconds: 120
  risk_level: active
  network_egress: [https]
  task_kind: enum
`;

const STARTER_SOURCE_TEMPLATE = `TOOL = {
    "name": "my-tool",
    "description": "One-line description of what this does",
    "risk_level": "passive",
    "task_kind": "enum",
    "network_egress": ["https"],
    "python_deps": ["httpx"],
    "args": [
        {"name": "target", "type": "string", "required": True, "scope_kind": "domain"},
    ],
    "timeout_seconds": 60,
}

import base64, json, os
import httpx

payload = json.loads(base64.b64decode(os.environ["RTD_ARGS_JSON"]))
target = payload["args"]["target"]

# do the work — print results to stdout, errors to stderr, exit 0/nonzero
resp = httpx.get(f"https://crt.sh/?q={target}&output=json", timeout=30)
for row in resp.json():
    print(row["name_value"])
`;

type Mode = "auto" | "form" | "yaml";

export function ToolUploader({
  onDone,
}: {
  onDone: () => void | Promise<void>;
}) {
  const [mode, setMode] = useState<Mode>("auto");
  const [result, setResult] = useState<ToolUploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submitManifest = useCallback(
    async (manifest: string, source: File | null) => {
      setError(null);
      try {
        const res = await uploadTool(manifest, source);
        setResult(res);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [],
  );

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Register a new tool</CardTitle>
        <CardDescription>
          Three ways to describe a tool — pick whichever fits.
        </CardDescription>
        <div className="mt-2 flex flex-wrap gap-1">
          <ModeTab
            active={mode === "auto"}
            onClick={() => setMode("auto")}
            icon={<Sparkles className="h-3.5 w-3.5" />}
            label="Auto-detect"
            hint="upload .py with a TOOL dict"
          />
          <ModeTab
            active={mode === "form"}
            onClick={() => setMode("form")}
            icon={<FileText className="h-3.5 w-3.5" />}
            label="Guided form"
            hint="fill each field"
          />
          <ModeTab
            active={mode === "yaml"}
            onClick={() => setMode("yaml")}
            icon={<FileCode className="h-3.5 w-3.5" />}
            label="YAML"
            hint="advanced / paste"
          />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {mode === "auto" && (
          <AutoDetectPanel onSubmit={submitManifest} />
        )}
        {mode === "form" && <GuidedFormPanel onSubmit={submitManifest} />}
        {mode === "yaml" && <YamlPanel onSubmit={submitManifest} />}

        {error && <p className="text-xs text-critical">{error}</p>}
        {result && (
          <div
            className={cn(
              "rounded-md border p-3 text-xs",
              result.validation_ok
                ? "border-emerald-500/50 bg-emerald-500/5 text-emerald-100"
                : "border-amber-500/50 bg-amber-500/5 text-amber-100",
            )}
          >
            <p className="font-medium">
              {result.validation_ok
                ? `Registered "${result.tool.name}" — static validation clean.`
                : `Registered "${result.tool.name}" — static validation flagged issues:`}
            </p>
            {!result.validation_ok && (
              <ul className="mt-1.5 list-disc pl-4">
                {result.validation_errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-muted-foreground">
              Status: draft. Approve from the list below.
            </p>
            <div className="mt-2 flex gap-2">
              <Button size="sm" variant="outline" onClick={() => void onDone()}>
                Done
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setResult(null)}
              >
                Register another
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ModeTab({
  active,
  onClick,
  icon,
  label,
  hint,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  hint: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex flex-col items-start gap-0.5 rounded-md border px-3 py-2 text-left transition-colors",
        active
          ? "border-emerald-500/50 bg-emerald-500/10 text-foreground"
          : "border-border/60 text-muted-foreground hover:border-foreground/40 hover:text-foreground",
      )}
    >
      <span className="flex items-center gap-1.5 text-sm font-medium">
        {icon}
        {label}
      </span>
      <span className="text-[10px] text-muted-foreground/70">{hint}</span>
    </button>
  );
}

// ── Auto-detect ────────────────────────────────────────────────────────────

function AutoDetectPanel({
  onSubmit,
}: {
  onSubmit: (manifest: string, source: File | null) => Promise<void>;
}) {
  const [source, setSource] = useState<File | null>(null);
  const [preview, setPreview] = useState<ToolInferResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [inferError, setInferError] = useState<string | null>(null);

  const onFile = async (f: File | null) => {
    setSource(f);
    setPreview(null);
    setInferError(null);
    if (!f) return;
    setBusy(true);
    try {
      const res = await inferToolManifest(f);
      setPreview(res);
    } catch (err) {
      setInferError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (!preview || !source) return;
    setBusy(true);
    try {
      await onSubmit(preview.manifest_yaml, source);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-border/60 bg-secondary/20 p-3 text-xs text-muted-foreground">
        Write a normal Python file with a{" "}
        <code className="rounded bg-background px-1 py-0.5 font-mono text-[11px]">
          TOOL = {"{...}"}
        </code>{" "}
        dict at the top. Backend reads it via AST — no imports, no
        library. Args come in as base64 in{" "}
        <code className="rounded bg-background px-1 py-0.5 font-mono text-[11px]">
          RTD_ARGS_JSON
        </code>
        .
        <details className="mt-2">
          <summary className="cursor-pointer text-foreground/80">
            Show template
          </summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-border bg-background p-2 font-mono text-[10px]">
            {STARTER_SOURCE_TEMPLATE}
          </pre>
        </details>
      </div>

      <input
        type="file"
        accept=".py,text/x-python,application/x-python-code"
        onChange={(e) => void onFile(e.target.files?.[0] ?? null)}
        className="text-xs"
      />
      {source && (
        <p className="text-[11px] text-muted-foreground">
          {source.name} · {source.size} bytes
        </p>
      )}
      {inferError && <p className="text-xs text-critical">{inferError}</p>}

      {preview && (
        <div className="space-y-2 rounded-md border border-border/60 bg-background p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm">
              Detected:{" "}
              <span className="font-medium">{preview.name}</span>
              <span className="ml-2 text-xs text-muted-foreground">
                {preview.kind} · {preview.lane}
              </span>
            </p>
            {preview.missing.length === 0 ? (
              <span className="rounded border border-emerald-500/50 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-emerald-200">
                complete
              </span>
            ) : (
              <span className="rounded border border-amber-500/50 bg-amber-500/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-amber-200">
                {preview.missing.length} missing
              </span>
            )}
          </div>
          {preview.description && (
            <p className="text-xs text-muted-foreground">
              {preview.description}
            </p>
          )}
          {preview.missing.length > 0 && (
            <div className="text-xs">
              <p className="mb-1 text-amber-200">
                Backend couldn&apos;t infer these — add them to the TOOL
                dict, or switch to the Guided form to fill them in:
              </p>
              <ul className="list-disc pl-4 text-muted-foreground">
                {preview.missing.map((m) => (
                  <li key={m}>
                    <code className="font-mono">{m}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {preview.warnings.length > 0 && (
            <p className="text-xs text-critical">
              {preview.warnings.join("; ")}
            </p>
          )}
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">
              Generated manifest
            </summary>
            <pre className="mt-1 max-h-48 overflow-auto rounded-md border border-border bg-background p-2 font-mono text-[10px] text-muted-foreground">
              {preview.manifest_yaml}
            </pre>
          </details>
          <Button
            size="sm"
            disabled={busy || preview.missing.length > 0}
            onClick={submit}
            title={
              preview.missing.length > 0
                ? "Missing required fields — add to TOOL dict or use Guided form"
                : "Register this tool"
            }
          >
            <Upload className="mr-1.5 h-3.5 w-3.5" />
            {busy ? "Uploading…" : "Register"}
          </Button>
        </div>
      )}
    </div>
  );
}

// ── Guided form ────────────────────────────────────────────────────────────

const EGRESS_TOKENS = ["none", "dns", "http", "https", "all"] as const;
type EgressToken = (typeof EGRESS_TOKENS)[number];

interface FormArg {
  name: string;
  type: "string" | "integer" | "boolean" | "enum";
  required: boolean;
  scope_kind: string;
  values: string; // comma-separated for enum
}

interface FormState {
  name: string;
  description: string;
  kind: "python" | "shell" | "binary";
  lane: "analyst" | "admin";
  entrypoint: string;
  timeout_seconds: number;
  risk_level: "passive" | "active" | "destructive";
  task_kind: "enum" | "scan" | "exploit";
  network_egress: EgressToken[];
  python_deps: string;
  args: FormArg[];
}

function GuidedFormPanel({
  onSubmit,
}: {
  onSubmit: (manifest: string, source: File | null) => Promise<void>;
}) {
  const [state, setState] = useState<FormState>({
    name: "",
    description: "",
    kind: "python",
    lane: "analyst",
    entrypoint: "main.py",
    timeout_seconds: 120,
    risk_level: "passive",
    task_kind: "enum",
    network_egress: ["none"],
    python_deps: "",
    args: [
      {
        name: "target",
        type: "string",
        required: true,
        scope_kind: "domain",
        values: "",
      },
    ],
  });
  const [source, setSource] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  const manifestYaml = useMemo(() => stateToYaml(state), [state]);

  const canSubmit =
    state.name.trim() !== "" &&
    (state.kind === "binary" || source !== null);

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    try {
      await onSubmit(manifestYaml, source);
    } finally {
      setBusy(false);
    }
  };

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setState((prev) => ({ ...prev, [k]: v }));

  const toggleEgress = (t: EgressToken) => {
    setState((prev) => ({
      ...prev,
      network_egress: prev.network_egress.includes(t)
        ? prev.network_egress.filter((x) => x !== t)
        : [...prev.network_egress.filter((x) => x !== "none" && t !== "none"), t],
    }));
  };

  const updateArg = (idx: number, patch: Partial<FormArg>) => {
    setState((prev) => ({
      ...prev,
      args: prev.args.map((a, i) => (i === idx ? { ...a, ...patch } : a)),
    }));
  };
  const addArg = () =>
    setState((prev) => ({
      ...prev,
      args: [
        ...prev.args,
        {
          name: "",
          type: "string",
          required: false,
          scope_kind: "",
          values: "",
        },
      ],
    }));
  const removeArg = (idx: number) =>
    setState((prev) => ({
      ...prev,
      args: prev.args.filter((_, i) => i !== idx),
    }));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <FormField label="Name" hint="Lowercase, hyphenated. Unique.">
          <input
            className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
            value={state.name}
            onChange={(e) => update("name", e.target.value)}
            placeholder="my-tool"
          />
        </FormField>
        <FormField label="Entrypoint" hint="Filename inside your source dir.">
          <input
            className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
            value={state.entrypoint}
            onChange={(e) => update("entrypoint", e.target.value)}
          />
        </FormField>
        <FormField label="Description" hint="Shown on the catalog card.">
          <input
            className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
            value={state.description}
            onChange={(e) => update("description", e.target.value)}
          />
        </FormField>
        <FormField label="Timeout (seconds)">
          <input
            type="number"
            min={1}
            max={3600}
            className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
            value={state.timeout_seconds}
            onChange={(e) =>
              update("timeout_seconds", Number(e.target.value) || 120)
            }
          />
        </FormField>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <FormField label="Kind">
          <RadioRow
            value={state.kind}
            options={["python", "shell", "binary"]}
            onChange={(v) => update("kind", v as FormState["kind"])}
          />
        </FormField>
        <FormField label="Lane">
          <RadioRow
            value={state.lane}
            options={["analyst", "admin"]}
            onChange={(v) => update("lane", v as FormState["lane"])}
          />
        </FormField>
        <FormField label="Task kind" hint="Charter gate for agents.">
          <RadioRow
            value={state.task_kind}
            options={["enum", "scan", "exploit"]}
            onChange={(v) => update("task_kind", v as FormState["task_kind"])}
          />
        </FormField>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <FormField label="Risk">
          <RadioRow
            value={state.risk_level}
            options={["passive", "active", "destructive"]}
            onChange={(v) =>
              update("risk_level", v as FormState["risk_level"])
            }
          />
        </FormField>
        <FormField label="Network egress" hint="Default deny-all.">
          <div className="flex flex-wrap gap-1.5">
            {EGRESS_TOKENS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => toggleEgress(t)}
                className={cn(
                  "rounded-md border px-2 py-0.5 text-xs",
                  state.network_egress.includes(t)
                    ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-100"
                    : "border-border/60 text-muted-foreground hover:border-foreground/40",
                )}
              >
                {t}
              </button>
            ))}
          </div>
        </FormField>
      </div>

      <FormField label="Python deps" hint="Comma-separated pip names.">
        <input
          className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm"
          value={state.python_deps}
          onChange={(e) => update("python_deps", e.target.value)}
          placeholder="httpx, dnspython"
        />
      </FormField>

      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-xs font-medium text-muted-foreground">
            Arguments
          </span>
          <Button size="sm" variant="outline" onClick={addArg}>
            + Add arg
          </Button>
        </div>
        <div className="space-y-2">
          {state.args.map((a, i) => (
            <div
              key={i}
              className="grid grid-cols-1 gap-2 rounded-md border border-border/60 bg-background p-2 md:grid-cols-6"
            >
              <input
                placeholder="name"
                className="h-7 rounded border border-border bg-background px-1.5 text-xs md:col-span-1"
                value={a.name}
                onChange={(e) => updateArg(i, { name: e.target.value })}
              />
              <select
                value={a.type}
                onChange={(e) =>
                  updateArg(i, { type: e.target.value as FormArg["type"] })
                }
                className="h-7 rounded border border-border bg-background px-1.5 text-xs md:col-span-1"
              >
                <option value="string">string</option>
                <option value="integer">integer</option>
                <option value="boolean">boolean</option>
                <option value="enum">enum</option>
              </select>
              <input
                placeholder="scope_kind (opt)"
                className="h-7 rounded border border-border bg-background px-1.5 text-xs md:col-span-1"
                value={a.scope_kind}
                onChange={(e) =>
                  updateArg(i, { scope_kind: e.target.value })
                }
              />
              <input
                placeholder={a.type === "enum" ? "v1,v2,v3" : "—"}
                disabled={a.type !== "enum"}
                className="h-7 rounded border border-border bg-background px-1.5 text-xs disabled:opacity-40 md:col-span-1"
                value={a.values}
                onChange={(e) => updateArg(i, { values: e.target.value })}
              />
              <label className="flex items-center gap-1 text-xs md:col-span-1">
                <input
                  type="checkbox"
                  checked={a.required}
                  onChange={(e) => updateArg(i, { required: e.target.checked })}
                  className="accent-emerald-500"
                />
                required
              </label>
              <button
                type="button"
                onClick={() => removeArg(i)}
                className="text-[11px] text-muted-foreground hover:text-critical md:col-span-1"
              >
                remove
              </button>
            </div>
          ))}
        </div>
      </div>

      {state.kind !== "binary" && (
        <FormField
          label="Source file"
          hint="Required for python / shell kinds."
        >
          <input
            type="file"
            accept=".py,.sh,.bash,text/plain,text/x-python,application/x-sh"
            onChange={(e) => setSource(e.target.files?.[0] ?? null)}
            className="text-xs"
          />
          {source && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              {source.name} · {source.size} bytes
            </p>
          )}
        </FormField>
      )}

      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">
          Preview manifest
        </summary>
        <pre className="mt-1 max-h-64 overflow-auto rounded-md border border-border bg-background p-2 font-mono text-[10px] text-muted-foreground">
          {manifestYaml}
        </pre>
      </details>

      <Button size="sm" disabled={!canSubmit || busy} onClick={submit}>
        <Upload className="mr-1.5 h-3.5 w-3.5" />
        {busy ? "Uploading…" : "Register"}
      </Button>
    </div>
  );
}

function FormField({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-muted-foreground">
        {label}
      </label>
      {children}
      {hint && (
        <p className="mt-0.5 text-[10px] text-muted-foreground/70">{hint}</p>
      )}
    </div>
  );
}

function RadioRow({
  value,
  options,
  onChange,
}: {
  value: string;
  options: readonly string[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          onClick={() => onChange(o)}
          className={cn(
            "rounded-md border px-2 py-0.5 text-xs",
            value === o
              ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-100"
              : "border-border/60 text-muted-foreground hover:border-foreground/40",
          )}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

function stateToYaml(s: FormState): string {
  // Manually emit YAML — small enough that we don't need a lib client-side.
  const args = s.args
    .filter((a) => a.name.trim() !== "")
    .map((a) => {
      const parts: string[] = [];
      parts.push(`    - name: ${a.name}`);
      parts.push(`      type: ${a.type}`);
      if (a.required) parts.push(`      required: true`);
      if (a.scope_kind) parts.push(`      scope_kind: ${a.scope_kind}`);
      if (a.type === "enum" && a.values.trim()) {
        const values = a.values
          .split(",")
          .map((v) => v.trim())
          .filter((v) => v);
        parts.push(`      values: [${values.join(", ")}]`);
      }
      return parts.join("\n");
    });

  const deps = s.python_deps
    .split(",")
    .map((d) => d.trim())
    .filter((d) => d);

  return [
    `apiVersion: rtd.tools/v1`,
    `kind: Tool`,
    `metadata:`,
    `  name: ${s.name}`,
    ...(s.description ? [`  description: ${s.description}`] : []),
    `spec:`,
    `  kind: ${s.kind}`,
    `  lane: ${s.lane}`,
    `  entrypoint: ${s.entrypoint}`,
    `  timeout_seconds: ${s.timeout_seconds}`,
    `  risk_level: ${s.risk_level}`,
    `  task_kind: ${s.task_kind}`,
    `  network_egress: [${s.network_egress.join(", ")}]`,
    ...(deps.length
      ? [`  python_deps: [${deps.map((d) => `"${d}"`).join(", ")}]`]
      : []),
    ...(args.length ? [`  args:`, ...args] : []),
    ``,
  ].join("\n");
}

// ── YAML paste ─────────────────────────────────────────────────────────────

function YamlPanel({
  onSubmit,
}: {
  onSubmit: (manifest: string, source: File | null) => Promise<void>;
}) {
  const [yaml, setYaml] = useState(STARTER_YAML);
  const [source, setSource] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await onSubmit(yaml, source);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <Textarea
        value={yaml}
        onChange={(e) => setYaml(e.target.value)}
        rows={14}
        className="font-mono text-xs"
      />
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          Source file (required for python / shell)
        </label>
        <input
          type="file"
          accept=".py,.sh,.bash,text/plain,text/x-python,application/x-sh"
          onChange={(e) => setSource(e.target.files?.[0] ?? null)}
          className="text-xs"
        />
        {source && (
          <p className="mt-1 text-[11px] text-muted-foreground">
            {source.name} · {source.size} bytes
          </p>
        )}
      </div>
      <Button size="sm" disabled={busy} onClick={submit}>
        <Upload className="mr-1.5 h-3.5 w-3.5" />
        {busy ? "Uploading…" : "Upload"}
      </Button>
    </div>
  );
}
