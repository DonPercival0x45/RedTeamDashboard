// v1.9.0: theme registry — 10 palettes across two appearance groups.
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
//
// `appearance` drives whether the `.dark` class rides on <html> — every
// `dark:` Tailwind variant across the app flips as a group so hardcoded
// colour pills stay legible on their respective backgrounds.

export type ThemeId =
  | "dark"
  | "light"
  | "high-contrast"
  | "solarized-dark"
  | "solarized-light"
  | "nord"
  | "dracula"
  | "gruvbox"
  | "tokyo-night"
  | "catppuccin-mocha"
  | "sandstone"
  | "sage";

export interface ThemeMeta {
  id: ThemeId;
  label: string;
  description: string;
  // "dark" (near-black background) vs "light" (near-white background).
  // Drives whether the `.dark` class is present on <html>, which flips
  // every Tailwind `dark:` variant across the app so hardcoded colour
  // pills / badges pick the right text ramp per theme.
  appearance: "dark" | "light";
  // Five representative HSL triples surfaced as a swatch strip in the
  // picker so the analyst can eyeball a theme without applying it. Order
  // is: background, surface, foreground, accent, danger.
  swatches: [string, string, string, string, string];
}

export const THEMES: ThemeMeta[] = [
  // ── Original v1.8.0 trio ──────────────────────────────────────────
  {
    id: "dark",
    label: "Dark (default)",
    description:
      "Monochromatic minimalism — all-black surfaces, grayscale text ramp, single ember-red accent.",
    appearance: "dark",
    swatches: [
      "0 0% 0%",
      "0 0% 9%",
      "0 0% 96%",
      "0 0% 92%",
      "358 75% 59%",
    ],
  },
  {
    id: "light",
    label: "Light",
    description:
      "Inverse of Dark — near-white surfaces, dark text, same ember accent for continuity.",
    appearance: "light",
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
    appearance: "dark",
    swatches: [
      "0 0% 0%",
      "0 0% 0%",
      "0 0% 100%",
      "48 100% 60%",
      "0 100% 60%",
    ],
  },
  // ── v1.9.0 additions ──────────────────────────────────────────────
  {
    id: "solarized-dark",
    label: "Solarized Dark",
    description: "Ethan Schoonover classic — deep teal surfaces with warm yellow + orange accents.",
    appearance: "dark",
    swatches: [
      "192 100% 11%",
      "192 81% 14%",
      "186 8% 55%",
      "45 100% 35%",
      "1 71% 52%",
    ],
  },
  {
    id: "solarized-light",
    label: "Solarized Light",
    description: "Solarized in reverse — cream paper with the same warm yellow + orange accents.",
    appearance: "light",
    swatches: [
      "44 87% 94%",
      "46 42% 88%",
      "196 13% 45%",
      "45 100% 35%",
      "1 71% 43%",
    ],
  },
  {
    id: "nord",
    label: "Nord",
    description: "Cool arctic palette — muted polar-night blues with frost-accent aurora.",
    appearance: "dark",
    swatches: [
      "220 16% 22%",
      "222 16% 28%",
      "219 27% 88%",
      "193 43% 67%",
      "354 42% 56%",
    ],
  },
  {
    id: "dracula",
    label: "Dracula",
    description: "Playful high-contrast — deep purple surfaces with pink + violet accents.",
    appearance: "dark",
    swatches: [
      "231 15% 18%",
      "232 14% 31%",
      "60 30% 96%",
      "265 89% 78%",
      "0 100% 67%",
    ],
  },
  {
    id: "gruvbox",
    label: "Gruvbox",
    description: "Warm earth-tone dark — burlap surfaces with mustard yellow + rust red accents.",
    appearance: "dark",
    swatches: [
      "195 6% 12%",
      "20 5% 22%",
      "43 59% 81%",
      "41 96% 58%",
      "6 96% 59%",
    ],
  },
  {
    id: "tokyo-night",
    label: "Tokyo Night",
    description: "Modern midnight — inky blue surfaces with luminous blue + purple accents.",
    appearance: "dark",
    swatches: [
      "235 16% 13%",
      "226 27% 21%",
      "226 34% 75%",
      "220 89% 72%",
      "349 89% 72%",
    ],
  },
  {
    id: "catppuccin-mocha",
    label: "Catppuccin Mocha",
    description: "Soft-pastel dark — cocoa surfaces with lavender mauve + rose accents.",
    appearance: "dark",
    swatches: [
      "240 21% 15%",
      "237 16% 23%",
      "227 68% 88%",
      "267 84% 81%",
      "343 81% 75%",
    ],
  },
  {
    id: "sandstone",
    label: "Sandstone",
    description: "Warm-tan light — parchment surfaces, dark chocolate text, caramel + amber accents. High contrast for reading long finding notes.",
    appearance: "light",
    swatches: [
      "40 40% 92%",
      "38 30% 85%",
      "25 30% 20%",
      "30 55% 40%",
      "12 65% 42%",
    ],
  },
  {
    id: "sage",
    label: "Sage",
    description: "Pale-green light — sage cream surfaces, deep forest text, teal-green accents. Easy on the eyes for long report reviews.",
    appearance: "light",
    swatches: [
      "90 35% 92%",
      "90 25% 85%",
      "145 45% 18%",
      "145 55% 28%",
      "4 55% 42%",
    ],
  },
];

export const DEFAULT_THEME: ThemeId = "dark";

// LocalStorage key + the storage schema is intentionally versioned so a
// future breaking change to the theme id vocabulary can be migrated cleanly.
export const THEME_STORAGE_KEY = "rtd-theme-v1";

// Enumerated at module-load so the pre-hydration script can validate
// stored ids against the current registry without hand-updating a
// string literal every time a theme lands.
export const THEME_IDS: ThemeId[] = THEMES.map((t) => t.id);

export function isThemeId(v: unknown): v is ThemeId {
  return typeof v === "string" && THEMES.some((t) => t.id === v);
}

// Whether ``.dark`` should live on <html> for the given theme. Every
// theme whose ``appearance === "dark"`` gets it so the app's dense
// forest of ``dark:`` Tailwind variants keeps working. Light themes
// clear it so those variants fall through to their base colours.
export function isDarkAppearance(id: ThemeId): boolean {
  const t = THEMES.find((x) => x.id === id);
  return t ? t.appearance === "dark" : true;
}
