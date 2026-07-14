"use client";

// v1.25.0: header-right identity strip trimmed to
//   [name]  [version pill]  [gear]  [Sign out]
// Every /settings/* entry lives inside the gear-opened modal;
// "What's new" is behind the version pill. The routed pages still
// work as deep-links for bookmarks.
import { Settings } from "lucide-react";
import { useState } from "react";
import { ApprovalInbox } from "@/components/approval-inbox";
import { Button } from "@/components/ui/button";
import { SettingsModal } from "@/components/settings/settings-modal";
import { WhatsNewModal } from "@/components/settings/whats-new-modal";
import { useAuth } from "@/lib/auth";
import { useReleases } from "@/lib/hooks";
import { currentVersion } from "@/lib/release-notes";

export function IdentityMenu() {
  const { enabled, identity, signOut } = useAuth();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [whatsNewOpen, setWhatsNewOpen] = useState(false);
  const { data: releases } = useReleases();

  // Best-effort version label. Falls back to a compact placeholder if
  // /releases.json hasn't returned yet so the pill still occupies its
  // slot instead of shifting layout on load.
  const versionLabel = releases ? currentVersion(releases) ?? "v?" : "v…";

  if (!identity) return null;
  return (
    <div className="flex items-center gap-2.5 text-sm">
      <span className="text-muted-foreground">
        {identity.name}
        {!enabled && (
          <span className="ml-1 text-xs text-muted-foreground/60">(dev)</span>
        )}
      </span>
      <ApprovalInbox />
      <button
        type="button"
        onClick={() => setWhatsNewOpen(true)}
        aria-label={`Current version ${versionLabel}. Click to see what's new.`}
        className="rounded border border-border px-2 py-0.5 text-xs font-mono text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
      >
        {versionLabel}
      </button>
      <button
        type="button"
        onClick={() => setSettingsOpen(true)}
        aria-label="Open settings"
        className="rounded border border-border p-1.5 text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
      >
        <Settings className="h-4 w-4" />
      </button>
      {enabled && (
        <Button variant="outline" size="sm" onClick={signOut}>
          Sign out
        </Button>
      )}
      <SettingsModal open={settingsOpen} onOpenChange={setSettingsOpen} />
      <WhatsNewModal open={whatsNewOpen} onOpenChange={setWhatsNewOpen} />
    </div>
  );
}
