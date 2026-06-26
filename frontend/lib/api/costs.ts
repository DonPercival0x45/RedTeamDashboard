import { request } from "./base";
import type { CostRollup } from "@/lib/types/cost";

export function getEngagementCosts(slug: string): Promise<CostRollup> {
  return request<CostRollup>(`/projects/${slug}/costs`);
}
