// v1.9.0: client-only ``useFontSize`` hook.
//
// The registry (FONT_SIZES + helpers) lives in ``lib/font-sizes.ts``
// (no "use client" directive) so the server-rendered layout's
// pre-hydration snippet can enumerate it — this file only carries the
// React hook that reads/writes the preference.
//
// Re-exports the registry types + values for callers that want both
// the hook and the type from one import site.
"use client";

import { useCallback, useEffect, useState } from "react";
import {
  DEFAULT_FONT_SIZE,
  FONT_SIZE_STORAGE_KEY,
  fontSizeCss,
  isFontSizeId,
  type FontSizeId,
} from "@/lib/font-sizes";

export {
  DEFAULT_FONT_SIZE,
  FONT_SIZES,
  FONT_SIZE_IDS,
  FONT_SIZE_STORAGE_KEY,
  fontSizeCss,
  isFontSizeId,
  type FontSizeId,
  type FontSizeMeta,
} from "@/lib/font-sizes";

function readApplied(): FontSizeId {
  if (typeof document === "undefined") return DEFAULT_FONT_SIZE;
  const attr = document.documentElement.getAttribute("data-font-size");
  return isFontSizeId(attr) ? attr : DEFAULT_FONT_SIZE;
}

export function useFontSize(): {
  size: FontSizeId;
  setSize: (next: FontSizeId) => void;
} {
  const [size, setSizeState] = useState<FontSizeId>(readApplied);

  useEffect(() => {
    setSizeState(readApplied());
  }, []);

  const setSize = useCallback((next: FontSizeId) => {
    if (typeof document !== "undefined") {
      const root = document.documentElement;
      root.setAttribute("data-font-size", next);
      root.style.fontSize = fontSizeCss(next);
    }
    try {
      window.localStorage.setItem(FONT_SIZE_STORAGE_KEY, next);
    } catch {
      // Privacy mode / disabled storage — no-op, session-scoped change stays.
    }
    setSizeState(next);
  }, []);

  return { size, setSize };
}
