// v1.8.0: server-safe pre-hydration snippet generator.
//
// Kept out of `lib/theme.ts` (which is a "use client" module) so the
// server-rendered layout can call this synchronously in <head>. The
// snippet itself runs entirely in the browser — reads localStorage
// before React hydrates and stamps `data-theme` on <html> so users
// don't see a flash of the SSR-default theme before their preference
// takes effect.
import { THEMES, THEME_STORAGE_KEY } from "@/lib/themes";

export function themePreHydrationScript(): string {
  // Also toggles the `.dark` class on <html> so every Tailwind `dark:`
  // variant across the app resolves to its light-mode counterpart when
  // the analyst picked a light-appearance theme. Dark-appearance themes
  // keep `.dark` on. The valid-id list is stamped in from the registry
  // so v1.9.0's seven additional themes don't require a script edit.
  const validIds = JSON.stringify(THEMES.map((t) => t.id));
  const lightIds = JSON.stringify(
    THEMES.filter((t) => t.appearance === "light").map((t) => t.id),
  );
  return `
(function(){try{var t=localStorage.getItem(${JSON.stringify(
    THEME_STORAGE_KEY,
  )});var V=${validIds};var L=${lightIds};if(V.indexOf(t)>=0){var h=document.documentElement;h.setAttribute("data-theme",t);if(L.indexOf(t)>=0){h.classList.remove("dark");}else{h.classList.add("dark");}}}catch(e){}})();
`.trim();
}
