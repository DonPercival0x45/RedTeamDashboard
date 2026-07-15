// v2.0.0 nav route stub. Real automation UI (tool catalog, categories,
// running jobs) lands in a later release; for now this page just
// signals "next up" with the shared PlaceholderPage.

import { PlaceholderPage, ASCII_CAT_PLAYING } from "@/components/placeholder-page";

export default function AutomationPage() {
  return (
    <PlaceholderPage
      title="Automation"
      tagline="Almost There ......"
      detail="Workflow catalog and one-click tool runs are the next focus."
      art={ASCII_CAT_PLAYING}
    />
  );
}
