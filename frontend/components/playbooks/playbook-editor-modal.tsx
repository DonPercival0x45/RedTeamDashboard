"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { QuickAddKey } from "@/components/quick-add-key";
import { QueryState } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useCreatePlaybookMutation,
  useCreatePlaybookVersionMutation,
  usePlaybook,
  usePlaybookCatalogOptions,
  useProviderKeys,
} from "@/lib/hooks";
import type {
  PlaybookCategory,
  PlaybookRead,
  PlaybookStepDraft,
} from "@/lib/types";

const CATEGORY_LABEL: Record<PlaybookCategory, string> = {
  discovery: "Discovery",
  enumeration: "Enumeration",
  posture: "Security posture",
  exposure: "Exposure",
  validation: "Validation",
  scope_review: "Scope review",
  other: "Other",
};
const ENTITY_LABEL: Record<string, string> = {
  domain: "Domain",
  subdomain: "Subdomain",
  host: "Host",
  ip: "IP address",
  cidr: "CIDR",
  url: "URL",
  email: "Email",
  scope: "Scope records",
};

function entityFamily(value: string): string {
  return value === "domain" || value === "subdomain" || value === "host"
    ? "domain"
    : value;
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120);
}

export function PlaybookEditorModal({
  playbook,
  onClose,
}: {
  playbook: PlaybookRead | null;
  onClose: () => void;
}) {
  const editing = playbook !== null;
  const detailQuery = usePlaybook(playbook?.slug ?? null);
  const optionsQuery = usePlaybookCatalogOptions();
  const providerKeysQuery = useProviderKeys();
  const createMutation = useCreatePlaybookMutation();
  const versionMutation = useCreatePlaybookVersionMutation(playbook?.slug ?? "");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<PlaybookCategory>("discovery");
  const [entityTypes, setEntityTypes] = useState<string[]>(["domain"]);
  const [requiresApproval, setRequiresApproval] = useState(false);
  const [steps, setSteps] = useState<PlaybookStepDraft[]>([]);
  const [addingCredential, setAddingCredential] = useState<string | null>(null);
  const [pendingEntityFamily, setPendingEntityFamily] = useState<string | null>(null);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [baseline, setBaseline] = useState(
    JSON.stringify({
      name: "",
      slug: "",
      description: "",
      category: "discovery",
      entityTypes: ["domain"],
      requiresApproval: false,
      steps: [],
    }),
  );
  const [error, setError] = useState<string | null>(null);
  const [initializedSlug, setInitializedSlug] = useState<string | null>(null);
  const [editingBase, setEditingBase] = useState<{
    id: string;
    version: number;
  } | null>(null);

  useEffect(() => {
    const detail = detailQuery.data;
    if (!editing || !detail || initializedSlug === detail.slug) return;
    setInitializedSlug(detail.slug);
    setEditingBase({ id: detail.id, version: detail.version });
    setName(detail.name);
    setSlug(detail.slug);
    setSlugTouched(true);
    setDescription(detail.description ?? "");
    setCategory(detail.category ?? "other");
    setEntityTypes(
      detail.applicable_entity_types?.length
        ? detail.applicable_entity_types
        : [detail.applies_to_asset_class],
    );
    setRequiresApproval(detail.active);
    const initialSteps = detail.steps.map((step) => ({
      tool_slug: step.tool_slug,
      source_step_id: step.id,
      description: step.description,
    }));
    setSteps(initialSteps);
    setBaseline(
      JSON.stringify({
        name: detail.name,
        slug: detail.slug,
        description: detail.description ?? "",
        category: detail.category ?? "other",
        entityTypes: detail.applicable_entity_types?.length
          ? detail.applicable_entity_types
          : [detail.applies_to_asset_class],
        requiresApproval: detail.active,
        steps: initialSteps,
      }),
    );
  }, [detailQuery.data, editing, initializedSlug]);

  const targetKind = entityFamily(entityTypes[0] ?? "domain");
  const compatibleTools = useMemo(
    () =>
      (optionsQuery.data?.tools ?? []).filter((tool) =>
        tool.target_kinds.includes(targetKind),
      ),
    [optionsQuery.data?.tools, targetKind],
  );
  const tools = useMemo(() => {
    const existingSlugs = new Set(steps.map((step) => step.tool_slug));
    return (optionsQuery.data?.tools ?? []).filter(
      (tool) => tool.target_kinds.includes(targetKind) || existingSlugs.has(tool.slug),
    );
  }, [optionsQuery.data?.tools, steps, targetKind]);
  const toolsBySlug = useMemo(
    () => new Map((optionsQuery.data?.tools ?? []).map((tool) => [tool.slug, tool])),
    [optionsQuery.data?.tools],
  );
  const requiredCredentials = useMemo(
    () =>
      Array.from(
        new Set(
          steps
            .map((step) => toolsBySlug.get(step.tool_slug)?.credential)
            .filter((value): value is string => !!value),
        ),
      ).sort(),
    [steps, toolsBySlug],
  );
  const configuredProviders = new Set(
    (providerKeysQuery.data ?? []).map((key) => key.provider.toLowerCase()),
  );
  const missingCredentials = providerKeysQuery.data
    ? requiredCredentials.filter(
        (credential) => !configuredProviders.has(credential.toLowerCase()),
      )
    : [];
  const credentialsReady =
    requiredCredentials.length === 0 ||
    (providerKeysQuery.data !== undefined && !providerKeysQuery.error);
  const activeTools = steps
    .map((step) => toolsBySlug.get(step.tool_slug))
    .filter((tool) => tool?.risk === "active");
  const busy = createMutation.isPending || versionMutation.isPending;
  const currentSnapshot = JSON.stringify({
    name,
    slug,
    description,
    category,
    entityTypes,
    requiresApproval,
    steps,
  });
  const requestClose = () => {
    if (currentSnapshot !== baseline) setDiscardOpen(true);
    else onClose();
  };

  const toggleEntityType = (value: string) => {
    const family = entityFamily(value);
    const currentFamily = entityFamily(entityTypes[0] ?? value);
    if (!entityTypes.includes(value) && family !== currentFamily && steps.length > 0) {
      setPendingEntityFamily(value);
      return;
    }
    setEntityTypes((current) => {
      if (current.includes(value)) {
        return current.length === 1
          ? current
          : current.filter((item) => item !== value);
      }
      const compatible = current.filter((item) => entityFamily(item) === family);
      return [...compatible, value];
    });
    if (family !== currentFamily) setSteps([]);
  };

  const addStep = () => {
    const tool = compatibleTools[0];
    if (!tool) return;
    setSteps((current) => [
      ...current,
      { tool_slug: tool.slug, description: tool.description },
    ]);
  };

  const moveStep = (index: number, direction: -1 | 1) => {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= steps.length) return;
    setSteps((current) => {
      const next = [...current];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
  };

  const submit = async () => {
    setError(null);
    if (!name.trim()) return setError("Name is required.");
    if (!editing && !slug.trim()) return setError("Slug is required.");
    if (entityTypes.length === 0) return setError("Select an applicable entity type.");
    if (steps.length === 0) return setError("Add at least one playbook step.");
    try {
      const body = {
        name: name.trim(),
        description: description.trim() || null,
        category,
        applicable_entity_types: entityTypes,
        active: requiresApproval,
        steps: steps.map((step) => ({
          tool_slug: step.tool_slug,
          source_step_id: step.source_step_id ?? null,
          description: step.description?.trim() || null,
        })),
      };
      if (editing && playbook && editingBase) {
        await versionMutation.mutateAsync({
          ...body,
          expected_supersedes_id: editingBase.id,
          expected_version: editingBase.version,
        });
      } else {
        await createMutation.mutateAsync({ ...body, slug: slug.trim() });
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <>
    <Dialog open onOpenChange={(open) => !open && !busy && requestClose()}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit playbook" : "Create playbook"}</DialogTitle>
          <DialogDescription>
            {editing
              ? `Publishing changes creates ${playbook?.slug} v${(playbook?.version ?? 0) + 1}; prior runs keep their original recipe.`
              : "Build a durable recipe from server-approved tools. No code deployment is required."}
          </DialogDescription>
        </DialogHeader>

        {(editing && detailQuery.data === undefined) || optionsQuery.data === undefined ? (
          <QueryState
            isLoading={detailQuery.isLoading || optionsQuery.isLoading}
            error={detailQuery.error ?? optionsQuery.error}
            loadingLabel="Loading playbook editor…"
            errorLabel="Could not load the playbook editor."
            onRetry={() => {
              void detailQuery.refetch();
              void optionsQuery.refetch();
            }}
            isRetrying={detailQuery.isFetching || optionsQuery.isFetching}
          />
        ) : (
          <div className="space-y-5">
            <section className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="playbook-name">Name</Label>
                <Input
                  id="playbook-name"
                  value={name}
                  onChange={(event) => {
                    setName(event.target.value);
                    if (!editing && !slugTouched) setSlug(slugify(event.target.value));
                  }}
                  maxLength={200}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="playbook-slug">Slug</Label>
                <Input
                  id="playbook-slug"
                  value={slug}
                  disabled={editing}
                  onChange={(event) => {
                    setSlug(slugify(event.target.value));
                    setSlugTouched(true);
                  }}
                  pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
                />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="playbook-description">Description</Label>
                <textarea
                  id="playbook-description"
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  rows={3}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="playbook-category">Category</Label>
                <select
                  id="playbook-category"
                  value={category}
                  onChange={(event) => setCategory(event.target.value as PlaybookCategory)}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                >
                  {(optionsQuery.data?.categories ?? []).map((value) => (
                    <option key={value} value={value}>
                      {CATEGORY_LABEL[value] ?? value}
                    </option>
                  ))}
                </select>
              </div>
              <label className="flex items-center gap-2 self-end rounded-md border border-border px-3 py-2 text-sm">
                <input
                  type="checkbox"
                  checked={requiresApproval || activeTools.length > 0}
                  disabled={activeTools.length > 0}
                  onChange={(event) => setRequiresApproval(event.target.checked)}
                />
                Require analyst approval before execution
              </label>
            </section>

            <section className="space-y-2">
              <div>
                <h3 className="text-sm font-medium">Applicable entity types</h3>
                <p className="text-xs text-muted-foreground">
                  Matching playbooks surface automatically from those Entity records.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {(optionsQuery.data?.entity_types ?? []).map((value) => (
                  <label
                    key={value}
                    className="flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs"
                  >
                    <input
                      type="checkbox"
                      checked={entityTypes.includes(value)}
                      onChange={() => toggleEntityType(value)}
                    />
                    {ENTITY_LABEL[value] ?? value}
                  </label>
                ))}
              </div>
            </section>

            <section className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-medium">Ordered steps</h3>
                  <p className="text-xs text-muted-foreground">
                    Targets, transport, risk, and safe arguments are enforced by the server.
                  </p>
                </div>
                <Button type="button" size="sm" variant="outline" onClick={addStep} disabled={!tools.length}>
                  <Plus className="mr-1 h-3.5 w-3.5" /> Add step
                </Button>
              </div>
              {steps.length === 0 ? (
                <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
                  Add at least one step to publish this playbook.
                </p>
              ) : (
                <ol className="space-y-2">
                  {steps.map((step, index) => {
                    const selectedTool = toolsBySlug.get(step.tool_slug);
                    return (
                      <li key={`${index}-${step.tool_slug}`} className="rounded-md border border-border p-3">
                        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                          <div>
                            <Label htmlFor={`playbook-tool-${index}`}>Tool {index + 1}</Label>
                            <select
                              id={`playbook-tool-${index}`}
                              value={step.tool_slug}
                              onChange={(event) =>
                                setSteps((current) =>
                                  current.map((item, itemIndex) =>
                                    itemIndex === index
                                      ? {
                                          tool_slug: event.target.value,
                                          source_step_id: null,
                                          description: toolsBySlug.get(event.target.value)?.description ?? null,
                                        }
                                      : item,
                                  ),
                                )
                              }
                              className="mt-1 flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                            >
                              {tools.map((tool) => (
                                <option
                                  key={tool.slug}
                                  value={tool.slug}
                                  disabled={
                                    !tool.target_kinds.includes(targetKind) &&
                                    tool.slug !== step.tool_slug
                                  }
                                >
                                  {tool.name} · {tool.transport}
                                  {tool.risk === "active" ? " · active" : ""}
                                </option>
                              ))}
                            </select>
                          </div>
                          <div>
                            <Label htmlFor={`playbook-step-description-${index}`}>Step description</Label>
                            <Input
                              id={`playbook-step-description-${index}`}
                              className="mt-1"
                              value={step.description ?? ""}
                              onChange={(event) =>
                                setSteps((current) =>
                                  current.map((item, itemIndex) =>
                                    itemIndex === index
                                      ? { ...item, description: event.target.value }
                                      : item,
                                  ),
                                )
                              }
                            />
                            {selectedTool?.credential ? (
                              <p className="mt-1 text-[11px] text-muted-foreground">
                                Requires requester key: {selectedTool.credential}
                              </p>
                            ) : null}
                          </div>
                          <div className="flex items-end gap-1">
                            <Button type="button" size="icon" variant="ghost" aria-label={`Move step ${index + 1} up`} disabled={index === 0} onClick={() => moveStep(index, -1)}>
                              <ArrowUp className="h-4 w-4" />
                            </Button>
                            <Button type="button" size="icon" variant="ghost" aria-label={`Move step ${index + 1} down`} disabled={index === steps.length - 1} onClick={() => moveStep(index, 1)}>
                              <ArrowDown className="h-4 w-4" />
                            </Button>
                            <Button type="button" size="icon" variant="ghost" aria-label={`Remove step ${index + 1}`} onClick={() => setSteps((current) => current.filter((_, itemIndex) => itemIndex !== index))}>
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              )}
            </section>

            {requiredCredentials.length > 0 ? (
              <section className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
                <div className="flex items-center gap-2">
                  <KeyRound className="h-4 w-4" />
                  <h3 className="text-sm font-medium">Requester-owned API keys</h3>
                </div>
                <p className="text-xs text-muted-foreground">
                  Keys are cached for your session and never stored in the playbook recipe.
                </p>
                <QueryState
                  isLoading={providerKeysQuery.data === undefined && providerKeysQuery.isLoading}
                  error={providerKeysQuery.error}
                  hasData={providerKeysQuery.data !== undefined}
                  loadingLabel="Checking requester credentials…"
                  errorLabel="Could not check requester credentials."
                  onRetry={() => void providerKeysQuery.refetch()}
                  isRetrying={providerKeysQuery.isFetching}
                  compact={providerKeysQuery.data !== undefined}
                />
                <div className="flex flex-wrap gap-2">
                  {requiredCredentials.map((credential) => {
                    const missing = missingCredentials.includes(credential);
                    const checked = providerKeysQuery.data !== undefined && !providerKeysQuery.error;
                    return (
                      <Button
                        key={credential}
                        type="button"
                        size="sm"
                        variant={missing ? "outline" : "ghost"}
                        disabled={!credentialsReady || !missing}
                        onClick={() => setAddingCredential(credential)}
                      >
                        {credential}: {checked ? (missing ? "Add key" : "Configured") : "Unavailable"}
                      </Button>
                    );
                  })}
                </div>
                {addingCredential ? (
                  <div className="rounded-md border border-border bg-background p-3">
                    <QuickAddKey
                      key={addingCredential}
                      initialProvider={addingCredential}
                      onCreated={async () => {
                        await providerKeysQuery.refetch();
                        setAddingCredential(null);
                      }}
                    />
                  </div>
                ) : null}
              </section>
            ) : null}

            {activeTools.length > 0 ? (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
                Active steps ({activeTools.map((tool) => tool?.name).join(", ")}) force approval regardless of the checkbox above.
              </p>
            ) : null}
            {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={requestClose} disabled={busy}>Cancel</Button>
          <Button type="button" onClick={() => void submit()} disabled={busy || optionsQuery.data === undefined || (editing && detailQuery.data === undefined)}>
            {busy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null}
            {editing ? "Publish new version" : "Create playbook"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <ConfirmDialog
      open={discardOpen}
      title="Discard playbook draft?"
      description="Your unpublished playbook changes will be lost."
      confirmLabel="Discard changes"
      onConfirm={onClose}
      onOpenChange={setDiscardOpen}
    />
    <ConfirmDialog
      open={pendingEntityFamily !== null}
      title="Change playbook target family?"
      description="The current steps are not valid for the new target family and will be cleared. The existing saved playbook is not changed until you publish."
      confirmLabel="Change and clear steps"
      onConfirm={() => {
        if (pendingEntityFamily) {
          setEntityTypes([pendingEntityFamily]);
          setSteps([]);
        }
        setPendingEntityFamily(null);
      }}
      onOpenChange={(open) => !open && setPendingEntityFamily(null)}
    />
    </>
  );
}
