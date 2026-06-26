// Orchestrator API — tasks, suggestions, and per-finding analysis (Phase 9).

import { request } from "./base";
import type {
  Task,
  TaskStatus,
  Suggestion,
  SuggestionStatus,
  AnalyzeFindingResponse,
  AcceptSuggestionResponse,
} from "@/lib/types/task";

export function analyzeFinding(
  findingId: string,
): Promise<AnalyzeFindingResponse> {
  return request<AnalyzeFindingResponse>(`/findings/${findingId}/analyze`, {
    method: "POST",
  });
}

export function listSuggestions(
  slug: string,
  status?: SuggestionStatus,
): Promise<Suggestion[]> {
  const q = status ? `?status=${status}` : "";
  return request<Suggestion[]>(`/projects/${slug}/suggestions${q}`);
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
  return request<Task[]>(`/projects/${slug}/tasks`);
}
