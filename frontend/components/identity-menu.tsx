"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

// Header-right identity slot. Shows the signed-in analyst, a link to the
// per-user settings page (where BYO LLM / MCP keys live), and the sign-out
// button under Entra; in dev mode it shows the dev identity with a muted
// "(dev)" tag.
export function IdentityMenu() {
  const { enabled, identity, signOut } = useAuth();
  if (!identity) return null;
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="text-muted-foreground">
        {identity.name}
        {!enabled && (
          <span className="ml-1 text-xs text-muted-foreground/60">(dev)</span>
        )}
      </span>
      <Link
        href="/settings/keys"
        className="text-xs text-muted-foreground hover:text-foreground"
      >
        Keys
      </Link>
      <Link
        href="/settings/suggestions"
        className="text-xs text-muted-foreground hover:text-foreground"
      >
        Suggestions
      </Link>
      <Link
        href="/settings/whats-new"
        className="text-xs text-muted-foreground hover:text-foreground"
      >
        What&apos;s new
      </Link>
      {enabled && (
        <Button variant="outline" size="sm" onClick={signOut}>
          Sign out
        </Button>
      )}
    </div>
  );
}
