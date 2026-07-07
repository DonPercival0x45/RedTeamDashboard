// v1.9.0: font-size preference — independent of theme.
//
// Applied to <html>'s inline `font-size` style. Tailwind's `text-xs/sm/
// base/lg/xl` classes are `rem`-based, and `rem` is relative to the root
// font-size, so bumping the root size scales every text element in the
// app uniformly. Also scales spacing that's expressed in `rem` (padding,
// gaps, etc.), giving the accessibility-heavy sizes real breathing room
// rather than crowded larger text.
//
// Persisted per browser in localStorage.rtd-font-size-v1. A companion
// pre-hydration snippet lives in ``lib/theme-preflight.ts`` so the size
// is stamped before React mounts to avoid a text-reflow flash.
"use client";

import { useCallback, useEffect, useState } from "react";

export type FontSizeId = "small" | "medium" | "large" | "xlarge";

export interface FontSizeMeta {
  id: FontSizeId;
  label: string;
  description: string;
  // The root font-size we stamp on <html>. Tailwind's default is 16px;
  // small drops one step, large/xlarge scale up for accessibility.
  cssValue: string;
}

export const FONT_SIZES: FontSizeMeta[] = [
  {
    id: "small",
    label: "Small",
    description: "Denser — fits more findings on screen at once.",
    cssValue: "14px",
  },
  {
    id: "medium",
    label: "Medium",
    description: "Default — matches the browser standard.",
    cssValue: "16px",
  },
  {
    id: "large",
    label: "Large",
    description: "Easier reading — good for long report reviews.",
    cssValue: "18px",
  },
  {
    id: "xlarge",
    label: "Extra Large",
    description: "Accessibility-first — maximum readability.",
    cssValue: "20px",
  },
];

export const DEFAULT_FONT_SIZE: FontSizeId = "medium";
export const FONT_SIZE_STORAGE_KEY = "rtd-font-size-v1";
export const FONT_SIZE_IDS: FontSizeId[] = FONT_SIZES.map((s) => s.id);

export function isFontSizeId(v: unknown): v is FontSizeId {
  return typeof v === "string" && FONT_SIZE_IDS.includes(v as FontSizeId);
}

export function fontSizeCss(id: FontSizeId): string {
  return (
    FONT_SIZES.find((s) => s.id === id)?.cssValue ??
    FONT_SIZES.find((s) => s.id === DEFAULT_FONT_SIZE)!.cssValue
  );
}

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

  // Resync from the DOM on mount in case the pre-hydration script
  // applied a different size than SSR guessed.
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
