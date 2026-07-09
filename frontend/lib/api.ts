// Fetch wrappers over the FastAPI surface.
//
// Phase 7: one backend (API_BASE_URL), identified analyst. Auth is resolved
// per request — an Entra Bearer token when SSO is configured, else a dev
// X-User-Id header for local work. No more per-call Source argument.

import { API_BASE_URL, DEV_USER, ENTRA_ENABLED } from "@/lib/config";
import { getAccessToken } from "@/lib/msal";
import type {
  AcceptSuggestionResponse,
  AnalyzeFindingResponse,
  TriageFindingResponse,
  Approval,
  ApprovalStatus,
  Attachment,
  Authorization,
  CostRollup,
  Engagement,
  EngagementStatus,
  EngagementTimeFrame,
  Entity,
  Finding,
  AdminUser,
  BurpImportResult,
  EngagementStatusResponse,
  FindingImport,
  FindingPhase,
  FindingSort,
  FindingSummaryEntry,
  FindingValidationStatus,
  StatusEntity,
  StatusKind,
  StepLogResponse,
  Integration,
  IntegrationCreate,
  IntegrationUpdate,
  Me,
  Observation,
  RoadmapSuggestion,
  RoadmapSuggestionStatus,
  RoadmapListFilters,
  CombineDetectResponse,
  BulkRankResponse,
  RankedRowRead,
  RunModel,
  RunStartResponse,
  Severity,
  ScopeKind,
  Suggestion,
  SuggestionStatus,
  Task,
  TaskStatus,
  UserRole,
  ContributionHeatmap,
  ContributionEntries,
  ContributionSource,
  ToolRead,
  ToolUploadResponse,
  ToolInferResponse,
  ToolKind,
  ToolLane,
  ToolStatus,
  ToolInvocationRead,
  OrchestratorTool,
} from "@/lib/types";

// Auth-only headers (no Content-Type — request() adds that for JSON bodies).
//
// v0.7.1: when ENTRA is enabled and getAccessToken() returns null (popup +
// redirect both failed, or page navigation is mid-flight), throw instead of
// returning an empty object. Returning {} used to fire an unauthenticated
// request that the backend rejected with "X-API-Key, Authorization: Bearer,
// or X-User-Id header required" — a confusing message that hit the analyst
// a beat before the MSAL redirect actually navigated. Throwing here keeps
// the API call from going out at all; the caller's catch surfaces a clear
// message while the redirect (already kicked off by getAccessToken) lands.
export async function authHeaders(): Promise<Record<string, string>> {
  if (ENTRA_ENABLED) {
    const token = await getAccessToken();
    if (!token) {
      throw new Error(
        "Re-authenticating with Entra — please retry once the page reloads.",
      );
    }
    return { Authorization: `Bearer ${token}` };
  }
  return { "X-User-Id": DEV_USER };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(await authHeaders()),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Engagements
// ---------------------------------------------------------------------------

export function listEngagements(
  status?: EngagementStatus,
): Promise<Engagement[]> {
  const q = status ? `?status=${status}` : "";
  return request<Engagement[]>(`/engagements${q}`);
}

export function getEngagement(slug: string): Promise<Engagement> {
  return request<Engagement>(`/engagements/${slug}`);
}

export function createEngagement(body: {
  name: string;
  slug?: string;
  description?: string;
  time_frame?: EngagementTimeFrame;
  start_date?: string | null;
  end_date?: string | null;
}): Promise<Engagement> {
  return request<Engagement>("/engagements", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function archiveEngagement(slug: string): Promise<Engagement> {
  return request<Engagement>(`/engagements/${slug}`, { method: "DELETE" });
}

export function flushEngagement(slug: string): Promise<void> {
  return request<void>(`/engagements/${slug}/flush`, { method: "POST" });
}

// ---------------------------------------------------------------------------
// Scope
// ---------------------------------------------------------------------------

export function listScope(slug: string) {
  return request<import("@/lib/types").ScopeItem[]>(
    `/engagements/${slug}/scope`,
  );
}

export function createScopeItem(
  slug: string,
  body: {
    kind: ScopeKind;
    value: string;
    is_exclusion?: boolean;
    note?: string | null;
    source?: "defined" | "found";
  },
) {
  return request<import("@/lib/types").ScopeItem>(
    `/engagements/${slug}/scope`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function deleteScopeItem(slug: string, scopeId: string): Promise<void> {
  return request<void>(`/engagements/${slug}/scope/${scopeId}`, {
    method: "DELETE",
  });
}

export function parseScope(
  text: string,
): Promise<import("@/lib/types").ScopeImportPreview> {
  return request<import("@/lib/types").ScopeImportPreview>("/scope/parse", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function importScope(
  slug: string,
  text: string,
): Promise<import("@/lib/types").ScopeImportResult> {
  return request<import("@/lib/types").ScopeImportResult>(
    `/engagements/${slug}/scope/import`,
    { method: "POST", body: JSON.stringify({ text }) },
  );
}

// ---------------------------------------------------------------------------
// Findings
// ---------------------------------------------------------------------------

export function listFindings(
  slug: string,
  filters?: {
    phase?: FindingPhase;
    status?: FindingValidationStatus;
    sort?: FindingSort;
  },
): Promise<Finding[]> {
  const q = new URLSearchParams();
  if (filters?.phase) q.set("phase", filters.phase);
  if (filters?.status) q.set("status", filters.status);
  if (filters?.sort) q.set("sort", filters.sort);
  const suffix = q.toString() ? `?${q.toString()}` : "";
  return request<Finding[]>(`/engagements/${slug}/findings${suffix}`);
}

// POST /engagements/{slug}/findings/import/burp — Burp Pro Issue Export XML.
//
// Uses FormData directly instead of the JSON `request()` helper because the
// browser MUST set the multipart Content-Type with its own boundary; fetch
// handles that automatically when `body` is a FormData and no Content-Type
// header is set on the request.
export function listFindingSummaries(
  findingId: string,
): Promise<FindingSummaryEntry[]> {
  return request<FindingSummaryEntry[]>(`/findings/${findingId}/summaries`);
}

export function createFindingSummary(
  findingId: string,
  body: string,
): Promise<FindingSummaryEntry> {
  return request<FindingSummaryEntry>(`/findings/${findingId}/summaries`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
}

export async function importFindingsFromBurp(
  slug: string,
  file: File,
  includeInfo: boolean = false,
): Promise<BurpImportResult> {
  const form = new FormData();
  form.append("file", file);
  const q = includeInfo ? "?include_info=true" : "";
  const response = await fetch(
    `${API_BASE_URL}/engagements/${slug}/findings/import/burp${q}`,
    {
      method: "POST",
      body: form,
      headers: { ...(await authHeaders()) },
    },
  );
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }
  return response.json() as Promise<BurpImportResult>;
}

export function listEntities(
  slug: string,
  filters?: { type?: string; q?: string },
): Promise<Entity[]> {
  const params = new URLSearchParams();
  if (filters?.type) params.set("type", filters.type);
  if (filters?.q) params.set("q", filters.q);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<Entity[]>(`/engagements/${slug}/entities${suffix}`);
}

export function validateFinding(
  findingId: string,
  decision: FindingValidationStatus,
  reason?: string,
): Promise<Finding> {
  return request<Finding>(`/findings/${findingId}/validate`, {
    method: "POST",
    body: JSON.stringify({ decision, reason }),
  });
}

// v1.4.0: manual "Add finding" — analyst types the finding into the
// Findings-tab modal. Distinct from importFindings (bulk) and the worker
// path (SSE-driven from live tool output).
export function createFinding(
  slug: string,
  body: import("@/lib/types").FindingCreate,
): Promise<Finding> {
  return request<Finding>(`/engagements/${slug}/findings`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// v1.4.0: kick the CorrelateAgent. Returns proposed groups; nothing
// merges until the analyst approves in the Correlate modal and each
// approval routes through mergeFindings().
export function correlateFindings(
  slug: string,
): Promise<import("@/lib/types").CorrelateResponse> {
  return request<import("@/lib/types").CorrelateResponse>(
    `/engagements/${slug}/findings/correlate`,
    { method: "POST" },
  );
}

// v1.4.0: fold `childIds` into the parent finding. Highest severity
// wins, child summaries append to the parent, children soft-deleted.
export function mergeFindings(
  parentId: string,
  childIds: string[],
): Promise<Finding> {
  return request<Finding>(`/findings/${parentId}/merge`, {
    method: "POST",
    body: JSON.stringify({ child_ids: childIds }),
  });
}

// v1.4.1: deterministic auto-grouping. Preview scans every ungrouped
// row, runs the v1.4.0 vocab, and returns proposed clusters. Nothing
// changes in the DB until apply is called with the approved keys.
export function regroupFindingsPreview(
  slug: string,
): Promise<import("@/lib/types").RegroupPreview> {
  return request<import("@/lib/types").RegroupPreview>(
    `/engagements/${slug}/findings/regroup/preview`,
    { method: "POST" },
  );
}

export function regroupFindingsApply(
  slug: string,
  groupKeys: string[],
): Promise<import("@/lib/types").RegroupApplyResult[]> {
  return request<import("@/lib/types").RegroupApplyResult[]>(
    `/engagements/${slug}/findings/regroup/apply`,
    {
      method: "POST",
      body: JSON.stringify({ group_keys: groupKeys }),
    },
  );
}

// v1.4.3: admin-only maintenance pass — rebuilds items[] from soft-deleted
// source rows, migrates legacy per-tool group keys (subfinder/crt_sh/dns)
// into the unified subdomains:{apex} shape, folds ungrouped rows that would
// match an existing parent's new key.
export function repairFindingGroups(
  slug: string,
): Promise<import("@/lib/types").RepairGroupsResult> {
  return request<import("@/lib/types").RepairGroupsResult>(
    `/engagements/${slug}/findings/repair-groups`,
    { method: "POST" },
  );
}

// ---------------------------------------------------------------------------
// Observations
// ---------------------------------------------------------------------------

export function listObservations(slug: string): Promise<Observation[]> {
  return request<Observation[]>(`/engagements/${slug}/observations`);
}

export function createObservation(
  slug: string,
  body: { content: string; phase?: FindingPhase | null },
): Promise<Observation> {
  return request<Observation>(`/engagements/${slug}/observations`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteObservation(observationId: string): Promise<void> {
  return request<void>(`/observations/${observationId}`, { method: "DELETE" });
}

// v1.4.8: observation ↔ finding links.
export function linkObservationFinding(
  observationId: string,
  findingId: string,
): Promise<Observation> {
  return request<Observation>(
    `/observations/${observationId}/findings/${findingId}`,
    { method: "POST" },
  );
}

export function unlinkObservationFinding(
  observationId: string,
  findingId: string,
): Promise<void> {
  return request<void>(
    `/observations/${observationId}/findings/${findingId}`,
    { method: "DELETE" },
  );
}

export function listObservationsForFinding(
  findingId: string,
): Promise<Observation[]> {
  return request<Observation[]>(`/findings/${findingId}/observations`);
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

export function startRun(
  slug: string,
  body: { prompt: string; model?: RunModel },
): Promise<RunStartResponse> {
  return request<RunStartResponse>(`/engagements/${slug}/runs`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

export function listApprovals(
  slug: string,
  status?: ApprovalStatus,
): Promise<Approval[]> {
  const q = status ? `?status=${status}` : "";
  return request<Approval[]>(`/engagements/${slug}/approvals${q}`);
}

export function decideApproval(
  approvalId: string,
  body: {
    approved: boolean;
    edited_args?: Record<string, unknown>;
    reason?: string;
    remember_for_session?: boolean;
  },
): Promise<Approval> {
  return request<Approval>(`/approvals/${approvalId}/decision`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Authorizations
// ---------------------------------------------------------------------------

export function listAuthorizations(
  engagementId: string,
  active?: boolean,
): Promise<Authorization[]> {
  const q = active === undefined ? "" : `?active=${active}`;
  return request<Authorization[]>(
    `/engagements/${engagementId}/authorizations${q}`,
  );
}

export function revokeAuthorization(
  authorizationId: string,
): Promise<Authorization> {
  return request<Authorization>(`/authorizations/${authorizationId}/revoke`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// BYO provider keys (LLM + MCP)
// ---------------------------------------------------------------------------

export function listProviderKeys(): Promise<
  import("@/lib/types").ProviderKey[]
> {
  return request<import("@/lib/types").ProviderKey[]>("/me/provider-keys");
}

export function createProviderKey(
  body: import("@/lib/types").ProviderKeyEntry,
): Promise<import("@/lib/types").ProviderKey> {
  return request<import("@/lib/types").ProviderKey>("/me/provider-keys", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function importProviderKeys(
  payload: import("@/lib/types").ProviderKeyImportPayload,
): Promise<import("@/lib/types").ProviderKeyImportResult> {
  return request<import("@/lib/types").ProviderKeyImportResult>(
    "/me/provider-keys/import",
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function deleteProviderKey(keyId: string): Promise<void> {
  return request<void>(`/me/provider-keys/${keyId}`, { method: "DELETE" });
}

export function updateProviderKey(
  keyId: string,
  body: Partial<import("@/lib/types").ProviderKeyEntry>,
): Promise<import("@/lib/types").ProviderKey> {
  return request<import("@/lib/types").ProviderKey>(
    `/me/provider-keys/${keyId}`,
    { method: "PATCH", body: JSON.stringify(body) },
  );
}

// Test an *unsaved* key + endpoint and pull the live model catalog. Used by
// the Quick Add form before the row is created.
export function probeUnsavedProviderKey(
  body: import("@/lib/types").ProviderKeyEntry & { api_key?: string | null },
): Promise<import("@/lib/types").ProviderKeyProbeResult> {
  return request<import("@/lib/types").ProviderKeyProbeResult>(
    "/me/provider-keys/probe",
    { method: "POST", body: JSON.stringify(body) },
  );
}

// Test an already-saved key by ID (plaintext stays in the backend).
export function probeSavedProviderKey(
  keyId: string,
): Promise<import("@/lib/types").ProviderKeyProbeResult> {
  return request<import("@/lib/types").ProviderKeyProbeResult>(
    `/me/provider-keys/${keyId}/probe`,
    { method: "POST" },
  );
}

// ---------------------------------------------------------------------------
// Orchestrator (Phase 9)
// ---------------------------------------------------------------------------

export function analyzeFinding(
  findingId: string,
): Promise<AnalyzeFindingResponse> {
  return request<AnalyzeFindingResponse>(`/findings/${findingId}/analyze`, {
    method: "POST",
  });
}

export function triageFinding(
  findingId: string,
): Promise<TriageFindingResponse> {
  return request<TriageFindingResponse>(`/findings/${findingId}/triage`, {
    method: "POST",
  });
}

export function listSuggestions(
  slug: string,
  status?: SuggestionStatus,
): Promise<Suggestion[]> {
  const q = status ? `?status=${status}` : "";
  return request<Suggestion[]>(`/engagements/${slug}/suggestions${q}`);
}

export function acceptSuggestion(
  suggestionId: string,
): Promise<AcceptSuggestionResponse> {
  return request<AcceptSuggestionResponse>(
    `/suggestions/${suggestionId}/accept`,
    { method: "POST" },
  );
}

export function dismissSuggestion(suggestionId: string): Promise<Suggestion> {
  return request<Suggestion>(`/suggestions/${suggestionId}/dismiss`, {
    method: "POST",
  });
}

export function listTasks(slug: string, _status?: TaskStatus): Promise<Task[]> {
  // status filter accepted for symmetry but currently always lists all
  return request<Task[]>(`/engagements/${slug}/tasks`);
}

// ---------------------------------------------------------------------------
// Costs (Phase 11)
// ---------------------------------------------------------------------------

export function getEngagementCosts(slug: string): Promise<CostRollup> {
  return request<CostRollup>(`/engagements/${slug}/costs`);
}

// ---------------------------------------------------------------------------
// Reports (PDF export)
// ---------------------------------------------------------------------------

export async function downloadEngagementReport(
  slug: string,
  opts: { omitExcluded?: boolean } = {},
): Promise<void> {
  const q = opts.omitExcluded ? "?omit_excluded=true" : "";
  const response = await fetch(`${API_BASE_URL}/engagements/${slug}/report${q}`, {
    headers: await authHeaders(),
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  const blob = await response.blob();
  const filename =
    _filenameFromDisposition(response.headers.get("content-disposition")) ??
    `${slug}-report.pdf`;
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

function _filenameFromDisposition(value: string | null): string | null {
  if (!value) return null;
  const match = /filename="?([^"]+)"?/i.exec(value);
  return match ? match[1] : null;
}

// ---------------------------------------------------------------------------
// Findings import + update
// ---------------------------------------------------------------------------

export function importFindings(
  slug: string,
  findings: FindingImport[],
): Promise<Finding[]> {
  return request<Finding[]>(`/engagements/${slug}/findings/import`, {
    method: "POST",
    body: JSON.stringify(findings),
  });
}

/**
 * Upload a Tenable Nessus .nessus v2 XML export. The backend parser
 * walks ReportItems and persists each as a Finding(status=pending_validation).
 *
 * Uses FormData directly instead of the JSON ``request()`` helper because
 * the browser MUST set the multipart Content-Type with its own boundary;
 * fetch handles that automatically when ``body`` is a FormData and no
 * Content-Type header is set on the request.
 */
export async function importFindingsNessus(
  slug: string,
  file: File,
  includeInfo: boolean = false,
): Promise<import("@/lib/types").NessusImportResult> {
  const form = new FormData();
  form.append("file", file);
  const q = includeInfo ? "?include_info=true" : "";
  const response = await fetch(
    `${API_BASE_URL}/engagements/${slug}/findings/import/nessus${q}`,
    {
      method: "POST",
      body: form,
      headers: { ...(await authHeaders()) },
    },
  );
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }
  return response.json() as Promise<import("@/lib/types").NessusImportResult>;
}

// ---------------------------------------------------------------------------
// Stored entities (Phase 10 Maltego import target)
// ---------------------------------------------------------------------------

export function listStoredEntities(
  slug: string,
): Promise<import("@/lib/types").StoredEntity[]> {
  return request<import("@/lib/types").StoredEntity[]>(
    `/engagements/${slug}/entities/stored`,
  );
}

/**
 * Upload a Maltego .mtgx graph export. The backend parses the zip-of-GraphML
 * server-side and UPSERTs each MaltegoEntity into the entities table.
 *
 * Same FormData multipart pattern as importFindingsNessus — fetch sets the
 * boundary itself, we must NOT override Content-Type.
 */
export async function importEntitiesMaltego(
  slug: string,
  file: File,
): Promise<import("@/lib/types").MaltegoImportResult> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(
    `${API_BASE_URL}/engagements/${slug}/entities/import/maltego`,
    {
      method: "POST",
      body: form,
      headers: { ...(await authHeaders()) },
    },
  );
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }
  return response.json() as Promise<import("@/lib/types").MaltegoImportResult>;
}

/**
 * Upload a DarkWeb data export (Dehashed JSON/CSV today; pluggable for
 * future sources). Multipart, same fetch-with-FormData pattern as the
 * Maltego importer — do NOT override Content-Type so the boundary lands.
 *
 * Format is auto-detected server-side by filename suffix.
 */
export async function importEntitiesDarkweb(
  slug: string,
  file: File,
  source: string = "dehashed",
): Promise<import("@/lib/types").DarkwebImportResult> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(
    `${API_BASE_URL}/engagements/${slug}/entities/import/darkweb?source=${encodeURIComponent(source)}`,
    {
      method: "POST",
      body: form,
      headers: { ...(await authHeaders()) },
    },
  );
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }
  return response.json() as Promise<
    import("@/lib/types").DarkwebImportResult
  >;
}

export function updateFinding(
  findingId: string,
  body: {
    title?: string;
    summary?: string | null;
    severity?: Severity;
    phase?: FindingPhase;
    // v1.4.0: pass null explicitly to clear the exclusion; omit the key
    // to leave it unchanged. `undefined` is skipped by JSON.stringify.
    exclusion?: import("@/lib/types").FindingExclusion | null;
    // v1.4.7: replace the whole tag list. Pass [] to clear.
    tags?: string[];
  },
): Promise<Finding> {
  return request<Finding>(`/findings/${findingId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Engagement JSON export
// ---------------------------------------------------------------------------

export async function downloadEngagementExport(
  slug: string,
  opts: { omitExcluded?: boolean } = {},
): Promise<void> {
  const q = opts.omitExcluded ? "?omit_excluded=true" : "";
  const data = await request<Record<string, unknown>>(
    `/engagements/${slug}/export${q}`,
  );
  const json = JSON.stringify(data, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slug}-export.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Finding attachments
// ---------------------------------------------------------------------------

export function listAttachments(findingId: string): Promise<Attachment[]> {
  return request<Attachment[]>(`/findings/${findingId}/attachments`);
}

export async function uploadAttachment(
  findingId: string,
  file: File,
): Promise<Attachment> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE_URL}/findings/${findingId}/attachments`, {
    method: "POST",
    // No Content-Type header — browser sets multipart boundary automatically.
    headers: await authHeaders(),
    body: form,
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }
  return response.json() as Promise<Attachment>;
}

export async function loadAttachmentBlob(attachmentId: string): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/attachments/${attachmentId}`, {
    headers: await authHeaders(),
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export function deleteAttachment(attachmentId: string): Promise<void> {
  return request<void>(`/attachments/${attachmentId}`, { method: "DELETE" });
}

export function deleteFinding(findingId: string): Promise<void> {
  return request<void>(`/findings/${findingId}`, { method: "DELETE" });
}

export interface BulkDeleteResult {
  deleted: number;
  skipped_missing: number;
  skipped_already_deleted: number;
}

export function bulkDeleteFindings(
  slug: string,
  findingIds: string[],
): Promise<BulkDeleteResult> {
  return request<BulkDeleteResult>(
    `/engagements/${slug}/findings/bulk-delete`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ finding_ids: findingIds }),
    },
  );
}

// ---------------------------------------------------------------------------
// /me + roadmap suggestions
// ---------------------------------------------------------------------------

export function getMe(): Promise<Me> {
  return request<Me>("/me");
}

// v1.4.11: set the analyst's default (provider, model) so the Start-a-run
// prompt pre-selects it. Either field may be null to clear.
export function updateMyPreferences(body: {
  default_llm_provider?: string | null;
  default_llm_model?: string | null;
}): Promise<Me> {
  return request<Me>("/me/preferences", {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function flushMyProviderKeys(): Promise<void> {
  // Wipe every cached BYO key for the acting user. Called from the
  // sign-out path so a tab close doesn't leave plaintext keys reachable
  // in Redis until TTL expiry. Best-effort — a failure here doesn't
  // block sign-out (Entra still tears down the session).
  return request<void>("/me/provider-keys", { method: "DELETE" });
}

export function listRoadmapSuggestions(
  filters: RoadmapListFilters = {},
): Promise<RoadmapSuggestion[]> {
  const qs = new URLSearchParams();
  if (filters.status) qs.set("status", filters.status);
  if (filters.priority_min != null)
    qs.set("priority_min", String(filters.priority_min));
  if (filters.priority_max != null)
    qs.set("priority_max", String(filters.priority_max));
  if (filters.include_unranked != null)
    qs.set("include_unranked", String(filters.include_unranked));
  if (filters.show_combined != null)
    qs.set("show_combined", String(filters.show_combined));
  const q = qs.toString();
  return request<RoadmapSuggestion[]>(
    `/roadmap-suggestions${q ? `?${q}` : ""}`,
  );
}

export function setRoadmapSuggestionPriority(
  id: string,
  priority: number | null,
): Promise<RoadmapSuggestion> {
  return request<RoadmapSuggestion>(
    `/roadmap-suggestions/${id}/priority`,
    {
      method: "PATCH",
      body: JSON.stringify({ priority }),
    },
  );
}

export function combineRoadmapSuggestions(
  primaryId: string,
  memberIds: string[],
): Promise<RoadmapSuggestion> {
  return request<RoadmapSuggestion>(
    `/roadmap-suggestions/${primaryId}/combine`,
    {
      method: "POST",
      body: JSON.stringify({ member_ids: memberIds }),
    },
  );
}

export function detectRoadmapCombines(): Promise<CombineDetectResponse> {
  return request<CombineDetectResponse>(
    "/roadmap-suggestions/detect-combines",
    { method: "POST" },
  );
}

export function rankRoadmapSuggestions(): Promise<BulkRankResponse> {
  return request<BulkRankResponse>("/roadmap-suggestions/rank", {
    method: "POST",
  });
}

export function applyRoadmapRankings(
  rankings: RankedRowRead[],
): Promise<BulkRankResponse> {
  return request<BulkRankResponse>("/roadmap-suggestions/rank/apply", {
    method: "POST",
    body: JSON.stringify({ rankings }),
  });
}

export function createRoadmapSuggestion(body: {
  body: string;
}): Promise<RoadmapSuggestion> {
  return request<RoadmapSuggestion>("/roadmap-suggestions", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function decideRoadmapSuggestion(
  id: string,
  decision: { status: "approved" | "rejected"; note?: string | null },
): Promise<RoadmapSuggestion> {
  return request<RoadmapSuggestion>(`/roadmap-suggestions/${id}/decision`, {
    method: "PATCH",
    body: JSON.stringify(decision),
  });
}

export function deleteRoadmapSuggestion(id: string): Promise<void> {
  return request<void>(`/roadmap-suggestions/${id}`, { method: "DELETE" });
}

export function reEvaluateRoadmapSuggestion(
  id: string,
): Promise<RoadmapSuggestion> {
  return request<RoadmapSuggestion>(
    `/roadmap-suggestions/${id}/re-evaluate`,
    { method: "POST" },
  );
}

// v1.1.0: admin marks an approved row shipped or reopens it.
// Orthogonal to status — the row stays "approved" (audit-preserved);
// only implemented_at / implemented_by_user_id flip.
export function setRoadmapSuggestionCompletion(
  id: string,
  completed: boolean,
): Promise<RoadmapSuggestion> {
  return request<RoadmapSuggestion>(
    `/roadmap-suggestions/${id}/completion`,
    {
      method: "PATCH",
      body: JSON.stringify({ completed }),
    },
  );
}

// ---------------------------------------------------------------------------
// Integrations (admin-only) — v0.9.0 multi-row by id
// ---------------------------------------------------------------------------

export function listIntegrations(): Promise<Integration[]> {
  return request<Integration[]>("/integrations");
}

export function getIntegration(id: string): Promise<Integration | null> {
  return request<Integration>(`/integrations/${id}`).catch((err) => {
    if (err instanceof Error && err.message.startsWith("404")) return null;
    throw err;
  });
}

export function createIntegration(
  body: IntegrationCreate,
): Promise<Integration> {
  return request<Integration>("/integrations", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateIntegration(
  id: string,
  body: IntegrationUpdate,
): Promise<Integration> {
  return request<Integration>(`/integrations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteIntegration(id: string): Promise<void> {
  return request<void>(`/integrations/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Admin users (admin-only)
// ---------------------------------------------------------------------------

export function listAdminUsers(): Promise<AdminUser[]> {
  return request<AdminUser[]>("/admin/users");
}

export function updateUserRole(
  userId: string,
  role: UserRole,
): Promise<AdminUser> {
  return request<AdminUser>(`/admin/users/${userId}/role`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

export function updateUserActive(
  userId: string,
  isActive: boolean,
): Promise<AdminUser> {
  return request<AdminUser>(`/admin/users/${userId}/active`, {
    method: "PATCH",
    body: JSON.stringify({ is_active: isActive }),
  });
}

export async function downloadRoadmapMarkdown(): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/roadmap-suggestions/export`, {
    headers: await authHeaders(),
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "ROADMAP.md";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export interface RoadmapPushResult {
  commit_sha: string | null;
  html_url: string | null;
  owner: string;
  repo: string;
  branch: string;
  path: string;
}

export function pushRoadmapToGitHub(): Promise<RoadmapPushResult> {
  return request<RoadmapPushResult>("/roadmap-suggestions/push", {
    method: "POST",
  });
}

// ── Status tab (v0.8.0) ───────────────────────────────────────────────────

export function getEngagementStatus(
  slug: string,
): Promise<EngagementStatusResponse> {
  return request<EngagementStatusResponse>(`/engagements/${slug}/status`);
}

export function retryTask(taskId: string): Promise<StatusEntity> {
  return request<StatusEntity>(`/tasks/${taskId}/retry`, { method: "POST" });
}

export function cancelTask(taskId: string): Promise<StatusEntity> {
  return request<StatusEntity>(`/tasks/${taskId}/cancel`, { method: "POST" });
}

export function cancelAgentExecution(executionId: string): Promise<StatusEntity> {
  return request<StatusEntity>(`/agent-executions/${executionId}/cancel`, {
    method: "POST",
  });
}

export function retryAgentExecution(
  executionId: string,
): Promise<StatusEntity> {
  return request<StatusEntity>(`/agent-executions/${executionId}/retry`, {
    method: "POST",
  });
}

// v1.2.0: fetch the per-entity step log. Lazy — only called when the
// analyst opens the Expand modal.
export function getStatusSteps(
  slug: string,
  kind: StatusKind,
  entityId: string,
): Promise<StepLogResponse> {
  // Kind maps to a URL segment. Enforced at the type level; runtime
  // still guards against future StatusKind additions.
  const segment =
    kind === "agent" ? "agents" : kind === "task" ? "tasks" : "approvals";
  return request<StepLogResponse>(
    `/engagements/${slug}/status/${segment}/${entityId}/steps`,
  );
}

// v1.2.0: tenant-global agent runs (planner rank/combine/re-evaluate).
// Not engagement-scoped — shows up on /settings/agent-runs.
export function listGlobalAgentRuns(): Promise<EngagementStatusResponse> {
  return request<EngagementStatusResponse>("/agent-runs");
}

export function getGlobalAgentRunSteps(
  executionId: string,
): Promise<StepLogResponse> {
  return request<StepLogResponse>(`/agent-runs/${executionId}/steps`);
}

// ---------------------------------------------------------------------------
// Contributions tab (v0.10.0)
// ---------------------------------------------------------------------------

export function getContributionsHeatmap(
  slug: string,
  filters: { actorId?: string | null; source?: ContributionSource | null } = {},
): Promise<ContributionHeatmap> {
  const qs = new URLSearchParams();
  if (filters.actorId) qs.set("actor_id", filters.actorId);
  if (filters.source) qs.set("source", filters.source);
  const q = qs.toString();
  return request<ContributionHeatmap>(
    `/engagements/${slug}/contributions/heatmap${q ? `?${q}` : ""}`,
  );
}

export function getContributionsEntries(
  slug: string,
  filters: {
    date?: string | null;
    actorId?: string | null;
    source?: ContributionSource | null;
    limit?: number;
    offset?: number;
  } = {},
): Promise<ContributionEntries> {
  const qs = new URLSearchParams();
  if (filters.date) qs.set("date", filters.date);
  if (filters.actorId) qs.set("actor_id", filters.actorId);
  if (filters.source) qs.set("source", filters.source);
  if (filters.limit != null) qs.set("limit", String(filters.limit));
  if (filters.offset != null) qs.set("offset", String(filters.offset));
  const q = qs.toString();
  return request<ContributionEntries>(
    `/engagements/${slug}/contributions/entries${q ? `?${q}` : ""}`,
  );
}

// ---------------------------------------------------------------------------
// Tools tab (v0.11.0)
// ---------------------------------------------------------------------------

export function listTools(
  filters: {
    kind?: ToolKind | null;
    lane?: ToolLane | null;
    status?: ToolStatus | null;
    // v1.11.0: filter by provenance. true → seeded first-party tools
    // (created_by_user_id IS NULL); false → analyst uploads only.
    first_party?: boolean | null;
  } = {},
): Promise<ToolRead[]> {
  const qs = new URLSearchParams();
  if (filters.kind) qs.set("kind", filters.kind);
  if (filters.lane) qs.set("lane", filters.lane);
  if (filters.status) qs.set("status", filters.status);
  if (filters.first_party === true) qs.set("first_party", "true");
  else if (filters.first_party === false) qs.set("first_party", "false");
  const q = qs.toString();
  return request<ToolRead[]>(`/tools${q ? `?${q}` : ""}`);
}

export function getTool(toolId: string): Promise<ToolRead> {
  return request<ToolRead>(`/tools/${toolId}`);
}

// v1.12.0: built-in orchestrator MCP tools (subfinder, dns_lookup, etc.).
// See backend/app/api/orchestrator_tools.py.
export function listOrchestratorTools(): Promise<OrchestratorTool[]> {
  return request<OrchestratorTool[]>("/orchestrator/tools");
}

export async function uploadTool(
  manifest: string,
  source: File | null,
): Promise<ToolUploadResponse> {
  const fd = new FormData();
  fd.set("manifest", manifest);
  if (source) fd.set("source", source);
  const headers = await authHeaders();
  const response = await fetch(`${API_BASE_URL}/tools`, {
    method: "POST",
    headers,
    body: fd,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  return (await response.json()) as ToolUploadResponse;
}

export async function inferToolManifest(
  source: File,
): Promise<ToolInferResponse> {
  const fd = new FormData();
  fd.set("source", source);
  const headers = await authHeaders();
  const response = await fetch(`${API_BASE_URL}/tools/infer`, {
    method: "POST",
    headers,
    body: fd,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  return (await response.json()) as ToolInferResponse;
}

export function approveTool(
  toolId: string,
  opts: { overrideValidation?: boolean; note?: string } = {},
): Promise<ToolRead> {
  return request<ToolRead>(`/tools/${toolId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      override_validation: opts.overrideValidation ?? false,
      note: opts.note ?? null,
    }),
  });
}

export function revokeTool(toolId: string): Promise<ToolRead> {
  return request<ToolRead>(`/tools/${toolId}/revoke`, { method: "POST" });
}

export function deleteTool(toolId: string): Promise<void> {
  return request<void>(`/tools/${toolId}`, { method: "DELETE" });
}

// Invocations
export function invokeTool(
  slug: string,
  toolId: string,
  args: Record<string, unknown>,
): Promise<ToolInvocationRead> {
  return request<ToolInvocationRead>(
    `/engagements/${slug}/tool-invocations`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool_id: toolId, args }),
    },
  );
}

export function listToolInvocations(
  slug: string,
): Promise<ToolInvocationRead[]> {
  return request<ToolInvocationRead[]>(
    `/engagements/${slug}/tool-invocations`,
  );
}

export function getToolInvocation(
  invocationId: string,
): Promise<ToolInvocationRead> {
  return request<ToolInvocationRead>(`/tool-invocations/${invocationId}`);
}
