"use client";

// First-run gate: until the operator adds at least one Source, redirect
// them to the Sources page. Replaces the Phase 0 UserIdGate.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Button } from "@/components/ui/button";
import { useSources } from "@/lib/source-context";

export function SourceGate({ children }: { children: React.ReactNode }) {
  const { ready, store } = useSources();
  const pathname = usePathname();

  if (!ready) {
    return (
      <main className="container py-6">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </main>
    );
  }

  // The Sources page is the one route allowed to render with no sources;
  // it's where the operator goes to add one.
  if (store.sources.length === 0 && pathname !== "/sources") {
    return (
      <main className="container flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-md space-y-4 rounded-lg border bg-card p-6 shadow-sm">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold">No sources configured</h2>
            <p className="text-sm text-muted-foreground">
              Add a tenant backend URL and a viewer-scoped API key to start
              reading findings, events, and grants from your deployment.
            </p>
          </div>
          <Button asChild className="w-full">
            <Link href="/sources">Add a source</Link>
          </Button>
        </div>
      </main>
    );
  }

  return <>{children}</>;
}
