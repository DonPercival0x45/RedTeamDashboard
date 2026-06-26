// Wire-format types for analyst observations.

import type { FindingPhase } from "./finding";

export interface Observation {
  id: string;
  content: string;
  phase: FindingPhase | null;
  created_by: string | null;
  created_at: string;
}
