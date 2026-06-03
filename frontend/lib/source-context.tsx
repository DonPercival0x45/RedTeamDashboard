"use client";

// React context for the currently-selected Source. Pages and components
// pull the active source from here; the layout's SourceProvider rehydrates
// from localStorage on mount and persists every mutation back.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  type Source,
  type SourceStore,
  loadStore,
  removeSource as removeSourceFn,
  resolveSource,
  saveStore,
  setDefaultSource as setDefaultSourceFn,
  upsertSource as upsertSourceFn,
} from "@/lib/sources";

interface SourceContextValue {
  ready: boolean;
  store: SourceStore;
  currentId: string | null;
  current: Source | null;
  selectSource: (id: string) => void;
  upsertSource: (source: Source, makeDefault?: boolean) => void;
  removeSource: (id: string) => void;
  setDefaultSource: (id: string) => void;
}

const SourceContext = createContext<SourceContextValue | null>(null);

export function SourceProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [store, setStore] = useState<SourceStore>({
    sources: [],
    defaultId: null,
  });
  const [currentId, setCurrentId] = useState<string | null>(null);

  // Hydrate from localStorage post-mount (SSR-safe).
  useEffect(() => {
    const initial = loadStore();
    setStore(initial);
    setCurrentId(initial.defaultId);
    setReady(true);
  }, []);

  const persist = useCallback((next: SourceStore) => {
    setStore(next);
    saveStore(next);
  }, []);

  const selectSource = useCallback(
    (id: string) => {
      if (store.sources.some((s) => s.id === id)) setCurrentId(id);
    },
    [store.sources],
  );

  const upsertSource = useCallback(
    (source: Source, makeDefault = false) => {
      const next = upsertSourceFn(store, source, makeDefault);
      persist(next);
      // Newly-added source becomes the current selection if nothing was
      // selected yet, or if the caller explicitly made it default.
      if (!currentId || makeDefault) setCurrentId(source.id);
    },
    [store, persist, currentId],
  );

  const removeSource = useCallback(
    (id: string) => {
      const next = removeSourceFn(store, id);
      persist(next);
      if (currentId === id) setCurrentId(next.defaultId);
    },
    [store, persist, currentId],
  );

  const setDefaultSource = useCallback(
    (id: string) => {
      persist(setDefaultSourceFn(store, id));
    },
    [store, persist],
  );

  const current = useMemo(
    () => resolveSource(store, currentId),
    [store, currentId],
  );

  const value: SourceContextValue = {
    ready,
    store,
    currentId,
    current,
    selectSource,
    upsertSource,
    removeSource,
    setDefaultSource,
  };

  return (
    <SourceContext.Provider value={value}>{children}</SourceContext.Provider>
  );
}

export function useSources(): SourceContextValue {
  const ctx = useContext(SourceContext);
  if (!ctx) {
    throw new Error("useSources must be used inside <SourceProvider>");
  }
  return ctx;
}

// Hook for components that should only render when a source is selected.
// Returns the resolved Source (never null); throw otherwise so callers don't
// have to litter their JSX with null-checks. Pages above this hook are
// responsible for gating on `ready` + `current`.
export function useCurrentSource(): Source {
  const { current } = useSources();
  if (!current) {
    throw new Error(
      "useCurrentSource called with no source selected — render the SourceGate first",
    );
  }
  return current;
}
