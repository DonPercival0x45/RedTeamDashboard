"use client";

// v1.11.0: bridge between the Scope-tab "Current Tools" panel and the
// <RunPrompt> textarea below it. Both live under the same engagement
// shell, but neither owns the other — a context is the smallest shared
// surface that lets ToolsPanel push a prompt string into RunPrompt
// without either component reaching across the tree with a ref.
//
// Contract:
//   <RunPromptBridgeProvider><ToolsPanel/><RunPrompt/></RunPromptBridgeProvider>
//
//   RunPrompt calls ``useRegisterRunPromptTarget(setPrompt)`` on mount,
//   which stores the setter in the provider's mutable box.
//
//   ToolsPanel calls ``useRunPromptBridge().insert(text)`` on click.
//   The provider forwards the call to whatever setter is currently
//   registered — a no-op if nothing has registered yet.
//
// The "mutable box" is deliberately outside React state: we want
// registration to NOT re-render the whole tree every time RunPrompt
// re-mounts, and we want insert() to always hit the latest setter
// even if it changed between renders.

import { createContext, useCallback, useContext, useEffect, useRef } from "react";

type PromptSetter = (next: string) => void;

interface Bridge {
  insert: (text: string) => void;
  register: (setter: PromptSetter | null) => void;
}

const RunPromptBridgeContext = createContext<Bridge | null>(null);

export function RunPromptBridgeProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  // Ref, not state — swapping the target should not cascade renders.
  const targetRef = useRef<PromptSetter | null>(null);

  const insert = useCallback((text: string) => {
    targetRef.current?.(text);
  }, []);

  const register = useCallback((setter: PromptSetter | null) => {
    targetRef.current = setter;
  }, []);

  return (
    <RunPromptBridgeContext.Provider value={{ insert, register }}>
      {children}
    </RunPromptBridgeContext.Provider>
  );
}

// Consumer hook — used by ToolsPanel (and anything else that wants to
// prefill the textarea). Returns a no-op bridge when called outside a
// provider so components render safely without asserting the wrap.
export function useRunPromptBridge(): Bridge {
  return (
    useContext(RunPromptBridgeContext) ?? { insert: () => {}, register: () => {} }
  );
}

// Producer hook — used by RunPrompt. Registers the setter on mount and
// clears it on unmount so late insert() calls don't leak into a
// previous mount.
export function useRegisterRunPromptTarget(setter: PromptSetter): void {
  const bridge = useContext(RunPromptBridgeContext);
  useEffect(() => {
    if (!bridge) return;
    bridge.register(setter);
    return () => bridge.register(null);
  }, [bridge, setter]);
}
