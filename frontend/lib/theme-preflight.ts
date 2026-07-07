// v1.8.0: server-safe pre-hydration snippet generator.
//
// Kept out of `lib/theme.ts` (which is a "use client" module) so the
// server-rendered layout can call this synchronously in <head>. The
// snippet itself runs entirely in the browser — reads localStorage
// before React hydrates and stamps `data-theme` on <html> so users
// don't see a flash of the SSR-default theme before their preference
// takes effect.
import {
  FONT_SIZES,
  FONT_SIZE_STORAGE_KEY,
} from "@/lib/font-size";
import { THEMES, THEME_STORAGE_KEY } from "@/lib/themes";

export function themePreHydrationScript(): string {
  // Handles two per-browser preferences in one head-inlined snippet:
  //   1. Theme  — stamps ``data-theme`` and toggles ``.dark`` on <html>
  //      so every Tailwind ``dark:`` variant flips as a group.
  //   2. Font size — stamps ``data-font-size`` and inline ``style.fontSize``
  //      so root-relative Tailwind text/spacing scales before paint.
  // Both id lists are stamped in from their registries so future
  // additions never require a script edit.
  const validThemeIds = JSON.stringify(THEMES.map((t) => t.id));
  const lightThemeIds = JSON.stringify(
    THEMES.filter((t) => t.appearance === "light").map((t) => t.id),
  );
  const fontSizeMap = JSON.stringify(
    Object.fromEntries(FONT_SIZES.map((s) => [s.id, s.cssValue])),
  );
  return `
(function(){var h=document.documentElement;try{var t=localStorage.getItem(${JSON.stringify(
    THEME_STORAGE_KEY,
  )});var V=${validThemeIds};var L=${lightThemeIds};if(V.indexOf(t)>=0){h.setAttribute("data-theme",t);if(L.indexOf(t)>=0){h.classList.remove("dark");}else{h.classList.add("dark");}}}catch(e){}try{var s=localStorage.getItem(${JSON.stringify(
    FONT_SIZE_STORAGE_KEY,
  )});var M=${fontSizeMap};if(s&&M[s]){h.setAttribute("data-font-size",s);h.style.fontSize=M[s];}}catch(e){}})();
`.trim();
}
