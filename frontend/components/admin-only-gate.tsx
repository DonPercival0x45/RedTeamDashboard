"use client";

// v2.10.0 — shared "you need admin" placeholder for pages that shouldn't
// render for user/guest roles. Non-admins reaching an admin route land here
// instead of a blank page; admins see the children.

import { ShieldAlert } from "lucide-react";
import { useMe } from "@/lib/hooks";
import type { ReactNode } from "react";

export function AdminOnlyGate({ children }: { children: ReactNode }) {
  const { data: me, isLoading } = useMe();
  if (isLoading) {
    return (
      <p className="text-sm text-muted-foreground">Checking permissions…</p>
    );
  }
  if (!me?.is_admin) {
    return (
      <div className="mx-auto max-w-xl rounded-lg border border-border bg-card/40 p-8 text-center">
        <ShieldAlert className="mx-auto h-8 w-8 text-muted-foreground" />
        <h1 className="mt-4 text-lg font-semibold">Admins only</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          This page manages tenant infrastructure and is restricted to
          accounts with the admin role. Ask a workspace admin to grant
          you the role or navigate back to Engagements.
        </p>
      </div>
    );
  }
  return <>{children}</>;
}
