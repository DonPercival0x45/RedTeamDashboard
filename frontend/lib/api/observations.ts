import { request } from "./base";
import type { Observation } from "@/lib/types/observation";
import type { FindingPhase } from "@/lib/types/finding";

export function listObservations(slug: string): Promise<Observation[]> {
  return request<Observation[]>(`/projects/${slug}/observations`);
}

export function createObservation(
  slug: string,
  body: { content: string; phase?: FindingPhase | null },
): Promise<Observation> {
  return request<Observation>(`/projects/${slug}/observations`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteObservation(observationId: string): Promise<void> {
  return request<void>(`/observations/${observationId}`, { method: "DELETE" });
}
