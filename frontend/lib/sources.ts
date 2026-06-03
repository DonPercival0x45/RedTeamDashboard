// Source connections — one entry per tenant the viewer can read from.
//
// Phase 6: the viewer is presentation-only. The operator pastes their
// tenant's backend URL + a viewer-scoped API key into the Sources page;
// every API call resolves the current Source from localStorage and sends
// the key as `X-API-Key`. Nothing leaves the browser except direct calls
// to the user's own tenant (no proxy, no viewer-side DB).

const STORAGE_KEY = "rtd.sources.v1";

export interface Source {
  id: string;
  name: string;
  url: string;
  apiKey: string;
}

export interface SourceStore {
  sources: Source[];
  defaultId: string | null;
}

function emptyStore(): SourceStore {
  return { sources: [], defaultId: null };
}

export function loadStore(): SourceStore {
  if (typeof window === "undefined") return emptyStore();
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return emptyStore();
  try {
    const parsed = JSON.parse(raw) as Partial<SourceStore>;
    const sources = Array.isArray(parsed.sources)
      ? parsed.sources.filter(isSource)
      : [];
    const defaultId =
      typeof parsed.defaultId === "string" &&
      sources.some((s) => s.id === parsed.defaultId)
        ? parsed.defaultId
        : sources[0]?.id ?? null;
    return { sources, defaultId };
  } catch {
    return emptyStore();
  }
}

function isSource(value: unknown): value is Source {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    typeof v.name === "string" &&
    typeof v.url === "string" &&
    typeof v.apiKey === "string"
  );
}

export function saveStore(store: SourceStore): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
}

export function resolveSource(
  store: SourceStore,
  id: string | null,
): Source | null {
  if (id) {
    const hit = store.sources.find((s) => s.id === id);
    if (hit) return hit;
  }
  if (store.defaultId) {
    return store.sources.find((s) => s.id === store.defaultId) ?? null;
  }
  return store.sources[0] ?? null;
}

// Mutations are pure functions over the store; callers persist with saveStore.

export function upsertSource(
  store: SourceStore,
  source: Source,
  makeDefault = false,
): SourceStore {
  const sources = store.sources.some((s) => s.id === source.id)
    ? store.sources.map((s) => (s.id === source.id ? source : s))
    : [...store.sources, source];
  const defaultId = makeDefault
    ? source.id
    : store.defaultId ?? sources[0]?.id ?? null;
  return { sources, defaultId };
}

export function removeSource(store: SourceStore, id: string): SourceStore {
  const sources = store.sources.filter((s) => s.id !== id);
  const defaultId =
    store.defaultId === id ? sources[0]?.id ?? null : store.defaultId;
  return { sources, defaultId };
}

export function setDefaultSource(store: SourceStore, id: string): SourceStore {
  if (!store.sources.some((s) => s.id === id)) return store;
  return { ...store, defaultId: id };
}

// Used by the Sources page when the operator types in a new source. Keeps
// IDs stable across sessions without dragging in uuid as a dep.
export function newSourceId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `src-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}
