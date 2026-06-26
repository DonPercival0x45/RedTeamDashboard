import { request } from "./base";
import type {
  ScopeItem,
  ScopeKind,
  ScopeImportPreview,
  ScopeImportResult,
} from "@/lib/types/project";

export function listScope(slug: string): Promise<ScopeItem[]> {
  return request<ScopeItem[]>(`/projects/${slug}/scope`);
}

export function createScopeItem(
  slug: string,
  body: {
    kind: ScopeKind;
    value: string;
    is_exclusion?: boolean;
    note?: string | null;
  },
): Promise<ScopeItem> {
  return request<ScopeItem>(`/projects/${slug}/scope`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteScopeItem(slug: string, scopeId: string): Promise<void> {
  return request<void>(`/projects/${slug}/scope/${scopeId}`, {
    method: "DELETE",
  });
}

export function parseScope(text: string): Promise<ScopeImportPreview> {
  return request<ScopeImportPreview>("/scope/parse", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function importScope(
  slug: string,
  text: string,
): Promise<ScopeImportResult> {
  return request<ScopeImportResult>(`/projects/${slug}/scope/import`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}
