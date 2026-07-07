// v1.8.0: server-safe pre-hydration snippet generator.
//
// Kept out of `lib/theme.ts` (which is a "use client" module) so the
// server-rendered layout can call this synchronously in <head>. The
// snippet itself runs entirely in the browser — reads localStorage
// before React hydrates and stamps `data-theme` on <html> so users
// don't see a flash of the SSR-default theme before their preference
// takes effect.
import { THEME_STORAGE_KEY } from "@/lib/themes";

export function themePreHydrationScript(): string {
  return `
(function(){try{var t=localStorage.getItem(${JSON.stringify(
    THEME_STORAGE_KEY,
  )});if(t==="dark"||t==="light"||t==="high-contrast"){document.documentElement.setAttribute("data-theme",t);}}catch(e){}})();
`.trim();
}
