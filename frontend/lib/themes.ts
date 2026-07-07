// v1.8.0: theme registry.
//
// Each theme is a full palette for the shadcn-style CSS variables that
// globals.css declares (background / foreground / card / popover / primary
// / secondary / muted / accent / destructive / critical / border / input /
// ring). Adding a theme = a new entry here + a matching
// `[data-theme="..."]` block in globals.css. The picker + hook read this
// registry as the source of truth for the display list.
//
// Values are HSL triples (e.g. "0 0% 96%") so they can slot straight into
// `hsl(var(--...))` calls without further parsing.

export type ThemeId = "dark" | "light" | "high-contrast";

export interface ThemeMeta {
  id: ThemeId;
  label: string;
  description: string;
  // Five representative HSL triples surfaced as a swatch strip in the
  // picker so the analyst can eyeball a theme without applying it. Order
  // is: background, surface, foreground, accent, danger.
  swatches: [string, string, string, string, string];
}

export const THEMES: ThemeMeta[] = [
  {
    id: "dark",
    label: "Dark (default)",
    description:
      "Monochromatic minimalism — all-black surfaces, grayscale text ramp, single ember-red accent.",
    swatches: [
      "0 0% 0%", // background
      "0 0% 9%", // muted
      "0 0% 96%", // foreground
      "0 0% 92%", // primary
      "358 75% 59%", // critical (ember)
    ],
  },
  {
    id: "light",
    label: "Light",
    description:
      "Inverse of Dark — near-white surfaces, dark text, same ember accent for continuity.",
    swatches: [
      "0 0% 100%",
      "0 0% 96%",
      "0 0% 6%",
      "0 0% 12%",
      "358 75% 52%",
    ],
  },
  {
    id: "high-contrast",
    label: "High Contrast",
    description:
      "Accessibility-first — pure black surfaces, pure white text, hot amber accent, thick borders.",
    swatches: [
      "0 0% 0%",
      "0 0% 0%",
      "0 0% 100%",
      "48 100% 60%",
      "0 100% 60%",
    ],
  },
];

export const DEFAULT_THEME: ThemeId = "dark";

// LocalStorage key + the storage schema is intentionally versioned so a
// future breaking change to the theme id vocabulary can be migrated cleanly.
export const THEME_STORAGE_KEY = "rtd-theme-v1";

export function isThemeId(v: unknown): v is ThemeId {
  return (
    typeof v === "string" && THEMES.some((t) => t.id === v)
  );
}
