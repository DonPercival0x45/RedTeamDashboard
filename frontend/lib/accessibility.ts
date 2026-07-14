// v1.25.0: analyst accessibility preferences.
//
// Three toggles are stored in localStorage and reflected as HTML root
// attrs so CSS + ARIA behaviour can key off them without prop-drilling:
//   data-reduced-motion="true" — disables non-essential animations +
//                                 respects prefers-reduced-motion CSS.
//   data-cb-severity="none|deuteranopia|protanopia|tritanopia"
//                                 — swaps the severity-badge palette
//                                 for a colorblind-safe alternative.
//   data-sr-hints="true"        — mounts ARIA-live announcers that
//                                 mirror status/toast surfaces for
//                                 screen readers.
//
// Pre-hydration script mirrors the theme pattern (see lib/theme.ts) so
// the attrs land on <html> BEFORE React mounts — no flash of animated
// content or misapplied palette.
"use client";

import { useCallback, useEffect, useState } from "react";

export const A11Y_STORAGE_KEY = "rtd.a11y.v1";

export type ColorblindPreset =
  | "none"
  | "deuteranopia"
  | "protanopia"
  | "tritanopia";

export type TimeDisplay = "local" | "utc";

export interface A11yPreferences {
  reducedMotion: boolean;
  colorblindSeverity: ColorblindPreset;
  screenReaderHints: boolean;
  timeDisplay: TimeDisplay;
}

export const DEFAULT_A11Y: A11yPreferences = {
  reducedMotion: false,
  colorblindSeverity: "none",
  screenReaderHints: false,
  timeDisplay: "local",
};

function isColorblindPreset(v: unknown): v is ColorblindPreset {
  return (
    v === "none" ||
    v === "deuteranopia" ||
    v === "protanopia" ||
    v === "tritanopia"
  );
}

function readStored(): A11yPreferences {
  if (typeof window === "undefined") return DEFAULT_A11Y;
  try {
    const raw = window.localStorage.getItem(A11Y_STORAGE_KEY);
    if (!raw) return DEFAULT_A11Y;
    const parsed = JSON.parse(raw) as Partial<A11yPreferences>;
    return {
      reducedMotion: parsed.reducedMotion === true,
      colorblindSeverity: isColorblindPreset(parsed.colorblindSeverity)
        ? parsed.colorblindSeverity
        : "none",
      screenReaderHints: parsed.screenReaderHints === true,
      timeDisplay: parsed.timeDisplay === "utc" ? "utc" : "local",
    };
  } catch {
    return DEFAULT_A11Y;
  }
}

function applyToRoot(prefs: A11yPreferences): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (prefs.reducedMotion) {
    root.setAttribute("data-reduced-motion", "true");
  } else {
    root.removeAttribute("data-reduced-motion");
  }
  root.setAttribute("data-cb-severity", prefs.colorblindSeverity);
  root.setAttribute("data-time-display", prefs.timeDisplay);
  if (prefs.screenReaderHints) {
    root.setAttribute("data-sr-hints", "true");
  } else {
    root.removeAttribute("data-sr-hints");
  }
}

export function useA11yPreferences(): {
  prefs: A11yPreferences;
  set: (next: Partial<A11yPreferences>) => void;
  reset: () => void;
} {
  const [prefs, setPrefs] = useState<A11yPreferences>(DEFAULT_A11Y);

  // Sync from localStorage on mount — pre-hydration script has already
  // stamped the root attrs, but React state needs the mirror.
  useEffect(() => {
    setPrefs(readStored());
  }, []);

  const set = useCallback((next: Partial<A11yPreferences>) => {
    setPrefs((prev) => {
      const merged: A11yPreferences = { ...prev, ...next };
      applyToRoot(merged);
      try {
        window.localStorage.setItem(A11Y_STORAGE_KEY, JSON.stringify(merged));
      } catch {
        // localStorage disabled — session-only change is fine.
      }
      return merged;
    });
  }, []);

  const reset = useCallback(() => {
    set(DEFAULT_A11Y);
  }, [set]);

  return { prefs, set, reset };
}

// Inlined into <head> before React mounts. Mirrors the theme
// pre-hydration approach so the attrs land synchronously — the very
// first paint honours the analyst's motion + colorblind + SR prefs
// without a flash of default palette or an animation the analyst asked
// to suppress.
export const A11Y_PRE_HYDRATION_SCRIPT = `
(function () {
  try {
    var raw = window.localStorage.getItem(${JSON.stringify(A11Y_STORAGE_KEY)});
    if (!raw) return;
    var p = JSON.parse(raw);
    var el = document.documentElement;
    if (p && p.reducedMotion === true) el.setAttribute("data-reduced-motion", "true");
    var cb = (p && typeof p.colorblindSeverity === "string") ? p.colorblindSeverity : "none";
    el.setAttribute("data-cb-severity", cb);
    if (p && p.screenReaderHints === true) el.setAttribute("data-sr-hints", "true");
    var td = (p && p.timeDisplay === "utc") ? "utc" : "local";
    el.setAttribute("data-time-display", td);
  } catch (_e) {}
})();
`;
