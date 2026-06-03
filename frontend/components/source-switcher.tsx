"use client";

// Header dropdown: pick which source the rest of the app reads from. Falls
// back to a "manage sources" link when none are configured yet.

import Link from "next/link";
import { useSources } from "@/lib/source-context";

export function SourceSwitcher() {
  const { ready, store, currentId, selectSource } = useSources();

  if (!ready) return null;

  if (store.sources.length === 0) {
    return (
      <Link
        href="/sources"
        className="text-xs text-muted-foreground hover:underline"
      >
        Add a source →
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-3 text-xs text-muted-foreground">
      <label className="flex items-center gap-2">
        <span>Source</span>
        <select
          value={currentId ?? ""}
          onChange={(event) => selectSource(event.target.value)}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs"
        >
          {store.sources.map((source) => (
            <option key={source.id} value={source.id}>
              {source.name}
              {source.id === store.defaultId ? " ★" : ""}
            </option>
          ))}
        </select>
      </label>
      <Link href="/sources" className="hover:underline">
        manage
      </Link>
    </div>
  );
}
