"use client";

// v1.8.0: theme picker page. Analyst chooses among the 3 shell themes
// (Dark / Light / High Contrast). Selection persists in localStorage
// (see lib/themes.ts) and is applied to <html data-theme="..."> so the
// CSS variables in globals.css swap over instantly. No server round-trip
// — theming is a per-browser preference.
//
// Layout mirrors the other /settings/* pages: back link, h1 +
// description, single scrollable column. The picker is a radio list;
// each row previews the theme via a 5-color swatch strip drawn with the
// theme's actual HSL values (see lib/themes.ts).
import Link from "next/link";
import { Palette } from "lucide-react";
import { FONT_SIZES, type FontSizeId, useFontSize } from "@/lib/font-size";
import { useTheme } from "@/lib/theme";
import { THEMES, type ThemeMeta } from "@/lib/themes";
import { cn } from "@/lib/utils";

export default function SettingsAppearancePage() {
  const { theme, setTheme } = useTheme();
  const { size, setSize } = useFontSize();

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-4 py-6">
      <div>
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← engagements
        </Link>
        <h1 className="mt-2 flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Palette className="h-6 w-6" />
          Appearance
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Pick the theme + font size this browser uses for the dashboard.
          Your choices are saved locally — they don&apos;t sync across
          devices or teammates.
        </p>
      </div>

      {/* v1.9.0: "Colors" section holds the theme picker. */}
      <section className="space-y-4">
        <h2 className="text-base font-semibold text-foreground">Colors</h2>
        <ThemeGroup label="Dark themes" appearance="dark" active={theme} setTheme={setTheme} />
        <ThemeGroup label="Light themes" appearance="light" active={theme} setTheme={setTheme} />
      </section>

      {/* v1.9.0: font-size picker sits under Colors. Segmented buttons
          keep the four choices compact + all-visible; clicking commits
          immediately via useFontSize. Independent of theme. */}
      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold text-foreground">Font size</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Scales every text + spacing rem uniformly across the app. Applies
            instantly.
          </p>
        </div>
        <FontSizePicker active={size} setSize={setSize} />
      </section>
    </div>
  );
}

// v1.9.0: segmented button row for font size. Four choices sit compact
// on one line; the active choice gets the critical-tinted active pill
// matching the theme picker's "Active" chip vocabulary.
function FontSizePicker({
  active,
  setSize,
}: {
  active: FontSizeId;
  setSize: (id: FontSizeId) => void;
}) {
  return (
    <div>
      <div
        role="radiogroup"
        aria-label="Font size"
        className="inline-flex flex-wrap gap-1 rounded-lg border border-border bg-muted/30 p-1"
      >
        {FONT_SIZES.map((s) => {
          const selected = s.id === active;
          return (
            <button
              key={s.id}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => setSize(s.id)}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                selected
                  ? "border border-critical/60 bg-critical/10 text-critical"
                  : "text-muted-foreground hover:bg-background hover:text-foreground",
              )}
              // Preview the size inline: the button's own text scales to
              // its cssValue so the analyst sees the outcome before
              // committing.
              style={{ fontSize: s.cssValue }}
            >
              {s.label}
            </button>
          );
        })}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {FONT_SIZES.find((s) => s.id === active)?.description}
      </p>
    </div>
  );
}

// v1.9.0: themes render as a grid of tiles — 4 across on desktop, 2 on
// small screens. Dark (8 themes) wraps into 2 rows of 4; Light (4
// themes) fits one row of 4. Each tile is a compact card with the
// theme name + swatch strip; hovering shows the description in the
// tooltip. Click anywhere on the tile commits the choice.
function ThemeGroup({
  label,
  appearance,
  active,
  setTheme,
}: {
  label: string;
  appearance: "dark" | "light";
  active: string;
  setTheme: (id: ThemeMeta["id"]) => void;
}) {
  const entries = THEMES.filter((t) => t.appearance === appearance);
  if (entries.length === 0) return null;
  return (
    <fieldset>
      <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </legend>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
        {entries.map((t) => {
          const selected = t.id === active;
          return (
            <label
              key={t.id}
              title={t.description}
              className={cn(
                "group flex cursor-pointer flex-col gap-2 rounded-lg border p-3 transition-colors",
                selected
                  ? "border-critical/60 bg-critical/5"
                  : "border-border hover:border-foreground/40 hover:bg-muted/40",
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <span className="text-sm font-medium text-foreground">
                  {t.label}
                </span>
                <input
                  type="radio"
                  name="theme"
                  value={t.id}
                  checked={selected}
                  onChange={() => setTheme(t.id)}
                  className="mt-0.5 shrink-0 accent-critical"
                  aria-label={`Select ${t.label}`}
                />
              </div>
              <SwatchStrip theme={t} />
              {selected && (
                <span className="self-start rounded-full border border-critical/40 bg-critical/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-critical">
                  Active
                </span>
              )}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

// Five swatches drawn with each theme's actual HSL values so the
// analyst previews the palette without applying it. Inline styles are
// intentional — we're rendering with values that AREN'T necessarily the
// currently-active theme, so we can't lean on CSS variables here.
// v1.9.0: swatches stretch to fill the tile so the palette reads
// clearly on the 4-across grid.
function SwatchStrip({ theme }: { theme: ThemeMeta }) {
  const labels: [string, string, string, string, string] = [
    "background",
    "surface",
    "foreground",
    "primary",
    "accent",
  ];
  return (
    <div className="flex gap-1">
      {theme.swatches.map((hsl, i) => (
        <div
          key={i}
          title={`${labels[i]} — hsl(${hsl})`}
          className="h-6 flex-1 rounded border border-border/60"
          style={{ backgroundColor: `hsl(${hsl})` }}
        />
      ))}
    </div>
  );
}
