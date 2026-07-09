"use client";

// v1.25.0: routed deep-link to the Accessibility panel. Renders the
// same body the Settings modal renders; the modal is the primary
// entry point but bookmarks + direct links still work.
import Link from "next/link";
import { AccessibilityPanel } from "@/components/settings/panels/accessibility-panel";

export default function SettingsAccessibilityPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
      <div>
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← engagements
        </Link>
      </div>
      <AccessibilityPanel inModal={false} />
    </div>
  );
}
