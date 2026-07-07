// v1.8.0: theme applier hook + pre-hydration snippet.
//
// The pre-hydration snippet lives here so the layout can inline it in
// <head> before React mounts. It reads localStorage and stamps
// data-theme on <html> synchronously, so users don't see a flash of the
// default theme before their preference kicks in.
"use client";

import { useCallback, useEffect, useState } from "react";
import {
  DEFAULT_THEME,
  THEME_STORAGE_KEY,
  isDarkAppearance,
  isThemeId,
  type ThemeId,
} from "@/lib/themes";

// Read whichever theme is already applied on <html>. During SSR this
// falls back to the default; on the client, it reflects what the
// pre-hydration script (see `themePreHydrationScript`) or a prior call
// already applied.
function readAppliedTheme(): ThemeId {
  if (typeof document === "undefined") return DEFAULT_THEME;
  const attr = document.documentElement.getAttribute("data-theme");
  return isThemeId(attr) ? attr : DEFAULT_THEME;
}

export function useTheme(): {
  theme: ThemeId;
  setTheme: (next: ThemeId) => void;
} {
  const [theme, setThemeState] = useState<ThemeId>(readAppliedTheme);

  // On mount, resync from the DOM in case the pre-hydration script
  // applied a different theme than SSR guessed. Cheap; only runs once.
  useEffect(() => {
    setThemeState(readAppliedTheme());
  }, []);

  const setTheme = useCallback((next: ThemeId) => {
    if (typeof document !== "undefined") {
      const root = document.documentElement;
      root.setAttribute("data-theme", next);
      // Keep `.dark` on <html> only when the picked theme's appearance
      // is dark. This flips every `dark:` Tailwind utility across the
      // app in one write.
      if (isDarkAppearance(next)) {
        root.classList.add("dark");
      } else {
        root.classList.remove("dark");
      }
    }
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // localStorage can throw in privacy modes — silently no-op so the
      // in-memory swap still works this session.
    }
    setThemeState(next);
  }, []);

  return { theme, setTheme };
}

// The pre-hydration script lives in ``lib/theme-preflight.ts`` (no
// "use client" pragma) so the server-rendered root layout can import it
// synchronously. Keep this hook here as the client-side counterpart.
