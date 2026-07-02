"use client";

// v1.0.0: TanStack Query provider at the app root. Centralizes the cache
// so navigation between routes is instant (cache-served) and refocusing a
// tab revalidates automatically.
//
// Defaults chosen to match the "stale-and-hydration" pain points that
// motivated the v0.x → v1.x flip:
//
//   staleTime: 30s          — under 30s, re-mounts serve from cache without
//                             a network round-trip. Above 30s, the cache is
//                             still SHOWN immediately but a background
//                             refetch runs and swaps in fresh data — the
//                             classic stale-while-revalidate behavior.
//   gcTime:    5min         — how long an unused query stays in memory. Keeps
//                             the "quick trip to Costs and back" case warm.
//   refetchOnWindowFocus     — true. Alt-tabbing back to the browser now
//                             shows current state, no F5 needed.
//   refetchOnReconnect       — true. If Wi-Fi blips, first click after
//                             re-connect pulls fresh data.
//   retry: 1                 — one silent retry; more than that and the user
//                             wants to know the API is down.
//
// Per-query overrides (polling intervals for hot entities like running tool
// invocations) live at the hook definition site, not here.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function QueryProvider({ children }: { children: React.ReactNode }) {
  // useState — not top-level — so the QueryClient survives HMR but is not
  // shared across users on the server.
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            gcTime: 5 * 60_000,
            refetchOnWindowFocus: true,
            refetchOnReconnect: true,
            retry: 1,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}
