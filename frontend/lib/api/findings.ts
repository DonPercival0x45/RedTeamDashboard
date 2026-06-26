import { request, authHeaders, API_BASE_URL } from "./base";
import type {
  Finding,
  FindingPhase,
  FindingValidationStatus,
  FindingImport,
  Severity,
  Attachment,
  Entity,
} from "@/lib/types/finding";

export function listFindings(
  slug: string,
  filters?: { phase?: FindingPhase; status?: FindingValidationStatus },
): Promise<Finding[]> {
  const q = new URLSearchParams();
  if (filters?.phase) q.set("phase", filters.phase);
  if (filters?.status) q.set("status", filters.status);
  const suffix = q.toString() ? `?${q.toString()}` : "";
  return request<Finding[]>(`/projects/${slug}/findings${suffix}`);
}

export function listEntities(
  slug: string,
  filters?: { type?: string; q?: string },
): Promise<Entity[]> {
  const params = new URLSearchParams();
  if (filters?.type) params.set("type", filters.type);
  if (filters?.q) params.set("q", filters.q);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<Entity[]>(`/projects/${slug}/entities${suffix}`);
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

export function importFindings(
  slug: string,
  findings: FindingImport[],
): Promise<Finding[]> {
  return request<Finding[]>(`/projects/${slug}/findings/import`, {
    method: "POST",
    body: JSON.stringify(findings),
  });
}

export function updateFinding(
  findingId: string,
  body: {
    title?: string;
    summary?: string | null;
    severity?: Severity;
    phase?: FindingPhase;
  },
): Promise<Finding> {
  return request<Finding>(`/findings/${findingId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function listAttachments(findingId: string): Promise<Attachment[]> {
  return request<Attachment[]>(`/findings/${findingId}/attachments`);
}

export async function uploadAttachment(
  findingId: string,
  file: File,
): Promise<Attachment> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(
    `${API_BASE_URL}/findings/${findingId}/attachments`,
    {
      method: "POST",
      // No Content-Type header — browser sets multipart boundary automatically.
      headers: await authHeaders(),
      body: form,
    },
  );
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
  if (!response.ok)
    throw new Error(`${response.status} ${response.statusText}`);
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export function deleteAttachment(attachmentId: string): Promise<void> {
  return request<void>(`/attachments/${attachmentId}`, { method: "DELETE" });
}
