"use client";

import Link from "next/link";
import { AlertCircle, HelpCircle, Key, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

// Header-right identity slot. Settings entries are icon-only (with hover
// tooltips + aria labels) to keep the chrome tight: a key for BYO
// credentials, a comment box for feedback, and an alert+help pair for
// "What's new" (the latest release notes).
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
        title="Provider keys"
        aria-label="Provider keys"
        className="text-muted-foreground transition-colors hover:text-foreground"
      >
        <Key className="h-4 w-4" />
      </Link>
      <Link
        href="/settings/feedback"
        title="Feedback"
        aria-label="Feedback"
        className="text-muted-foreground transition-colors hover:text-foreground"
      >
        <MessageSquare className="h-4 w-4" />
      </Link>
      <Link
        href="/settings/whats-new"
        title="What's new"
        aria-label="What's new"
        className="inline-flex items-center text-muted-foreground transition-colors hover:text-foreground"
      >
        <AlertCircle className="h-4 w-4" />
        <HelpCircle className="-ml-1.5 h-4 w-4" />
      </Link>
      {enabled && (
        <Button variant="outline" size="sm" onClick={signOut}>
          Sign out
        </Button>
      )}
    </div>
  );
}
