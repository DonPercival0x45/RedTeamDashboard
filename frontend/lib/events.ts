// SSE wrapper around the backend's /engagements/{slug}/events feed.
//
// Uses @microsoft/fetch-event-source because the standard EventSource API
// can't send custom headers (we need X-API-Key). fetch-event-source also
// handles reconnect + Last-Event-ID for us.

import { fetchEventSource } from "@microsoft/fetch-event-source";
import type { Source } from "@/lib/sources";
import type { RunEvent } from "@/lib/types";

export interface SubscribeOptions {
  source: Source;
  slug: string;
  thread?: string;
  onEvent: (event: RunEvent, sseId: string | undefined) => void;
  onError?: (err: unknown) => void;
  onOpen?: () => void;
  signal: AbortSignal;
  lastEventId?: string;
}

export function subscribeToEvents(opts: SubscribeOptions): Promise<void> {
  const url = new URL(`${opts.source.url}/engagements/${opts.slug}/events`);
  if (opts.thread) url.searchParams.set("thread", opts.thread);

  return fetchEventSource(url.toString(), {
    method: "GET",
    headers: {
      "X-API-Key": opts.source.apiKey,
      ...(opts.lastEventId ? { "Last-Event-ID": opts.lastEventId } : {}),
    },
    signal: opts.signal,
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
    },
  });
}
