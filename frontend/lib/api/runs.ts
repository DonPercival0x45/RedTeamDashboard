import { request } from "./base";
import type { RunModel, RunStartResponse } from "@/lib/types/project";

export function startRun(
  slug: string,
  body: { prompt: string; model?: RunModel },
): Promise<RunStartResponse> {
  return request<RunStartResponse>(`/projects/${slug}/runs`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
