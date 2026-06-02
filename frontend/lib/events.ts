// SSE wrapper around the backend's /engagements/{slug}/events feed.
//
// Uses @microsoft/fetch-event-source because the standard EventSource API
// can't send custom headers (we need X-User-Id). fetch-event-source also
// gives us cleaner reconnect + Last-Event-ID handling than rolling our own.

import { fetchEventSource } from "@microsoft/fetch-event-source";
import { API_BASE } from "@/lib/api";
import { getUserId } from "@/lib/user";
import type { RunEvent } from "@/lib/types";

export interface SubscribeOptions {
  slug: string;
  thread?: string;
  onEvent: (event: RunEvent, sseId: string | undefined) => void;
  onError?: (err: unknown) => void;
  onOpen?: () => void;
  signal: AbortSignal;
  lastEventId?: string;
}

export function subscribeToEvents(opts: SubscribeOptions): Promise<void> {
  const userId = getUserId();
  if (!userId) {
    return Promise.reject(new Error("user id not set"));
  }

  const url = new URL(`${API_BASE}/engagements/${opts.slug}/events`);
  if (opts.thread) url.searchParams.set("thread", opts.thread);

  return fetchEventSource(url.toString(), {
    method: "GET",
    headers: {
      "X-User-Id": userId,
      ...(opts.lastEventId ? { "Last-Event-ID": opts.lastEventId } : {}),
    },
    signal: opts.signal,
    // Keep the connection alive across tab backgrounding.
    openWhenHidden: true,
    onopen: async (response) => {
      if (!response.ok) {
        throw new Error(`SSE open failed: ${response.status}`);
      }
      opts.onOpen?.();
    },
    onmessage: (msg) => {
      if (!msg.data) return;
      try {
        const payload = JSON.parse(msg.data) as RunEvent;
        opts.onEvent(payload, msg.id || undefined);
      } catch {
        // ignore malformed frames
      }
    },
    onerror: (err) => {
      opts.onError?.(err);
      // Let the library auto-reconnect (default behavior).
    },
  });
}
