"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import {
  AlertCircle,
  Globe,
  HelpCircle,
  Key,
  MessageSquare,
  UserCog,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { getMe } from "@/lib/api";
import { useAuth } from "@/lib/auth";

// Hover tooltip for the icon-only menu entries. Pure CSS via group-hover
// so it works without JS state; appears below the icon with a small
// pointer arrow. focus-within also reveals it so keyboard nav surfaces
// the label.
function IconLink({
  href,
  label,
  children,
}: {
  href: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="group relative">
      <Link
        href={href}
        aria-label={label}
        className="inline-flex items-center text-muted-foreground transition-colors hover:text-foreground"
      >
        {children}
      </Link>
      <span
        role="tooltip"
        className="pointer-events-none invisible absolute left-1/2 top-full z-50 mt-2 -translate-x-1/2 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs text-foreground opacity-0 shadow-md transition-opacity duration-100 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
      >
        {label}
      </span>
    </div>
  );
}

// Header-right identity slot. Settings entries are icon-only with a
// styled hover-tooltip that names each one — keeps the chrome tight
// without forcing the analyst to learn the icons cold.
export function IdentityMenu() {
  const { enabled, identity, signOut } = useAuth();
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    if (!identity) return;
    void getMe()
      .then((m) => setIsAdmin(m.is_admin))
      .catch(() => setIsAdmin(false));
  }, [identity]);

  if (!identity) return null;
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="text-muted-foreground">
        {identity.name}
        {!enabled && (
          <span className="ml-1 text-xs text-muted-foreground/60">(dev)</span>
        )}
      </span>
      <IconLink href="/settings/keys" label="Provider keys">
        <Key className="h-4 w-4" />
      </IconLink>
      <IconLink href="/settings/feedback" label="Feedback">
        <MessageSquare className="h-4 w-4" />
      </IconLink>
      <IconLink href="/settings/whats-new" label="What's new">
        <AlertCircle className="h-4 w-4" />
        <HelpCircle className="-ml-1.5 h-4 w-4" />
      </IconLink>
      {isAdmin && (
        <>
          <IconLink href="/settings/integrations" label="Integrations (admin)">
            <Globe className="h-4 w-4" />
          </IconLink>
          <IconLink href="/settings/management" label="Management (admin)">
            <UserCog className="h-4 w-4" />
          </IconLink>
        </>
      )}
      {enabled && (
        <Button variant="outline" size="sm" onClick={signOut}>
          Sign out
        </Button>
      )}
    </div>
  );
}
