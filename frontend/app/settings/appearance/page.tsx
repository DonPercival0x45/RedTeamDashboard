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
import { useTheme } from "@/lib/theme";
import { THEMES, type ThemeMeta } from "@/lib/themes";
import { cn } from "@/lib/utils";

export default function SettingsAppearancePage() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
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
          Pick the theme this browser uses for the dashboard. Your choice is
          saved locally — it doesn&apos;t sync across devices or teammates.
        </p>
      </div>

      <ThemeGroup label="Dark themes" appearance="dark" active={theme} setTheme={setTheme} />
      <ThemeGroup label="Light themes" appearance="light" active={theme} setTheme={setTheme} />
    </div>
  );
}

// v1.9.0: themes are grouped by appearance so a 10-item list stays
// scannable. The picker header labels each group; radio rows keep the
// same swatch-strip preview as v1.8.0.
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
    <fieldset className="space-y-2">
      <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </legend>
      {entries.map((t) => {
        const selected = t.id === active;
        return (
          <label
            key={t.id}
            className={cn(
              "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors",
              selected
                ? "border-critical/60 bg-critical/5"
                : "border-border hover:border-foreground/40 hover:bg-muted/40",
            )}
          >
            <input
              type="radio"
              name="theme"
              value={t.id}
              checked={selected}
              onChange={() => setTheme(t.id)}
              className="mt-1.5 accent-critical"
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-sm font-medium text-foreground">
                  {t.label}
                  {selected && (
                    <span className="ml-2 rounded-full border border-critical/40 bg-critical/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-critical">
                      Active
                    </span>
                  )}
                </p>
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {t.description}
              </p>
              <SwatchStrip theme={t} />
            </div>
          </label>
        );
      })}
    </fieldset>
  );
}

// Five swatches drawn with each theme's actual HSL values so the
// analyst previews the palette without applying it. Inline styles are
// intentional — we're rendering with values that AREN'T necessarily the
// currently-active theme, so we can't lean on CSS variables here.
function SwatchStrip({ theme }: { theme: ThemeMeta }) {
  const labels: [string, string, string, string, string] = [
    "background",
    "surface",
    "foreground",
    "primary",
    "accent",
  ];
  return (
    <div className="mt-2 flex gap-1.5">
      {theme.swatches.map((hsl, i) => (
        <div
          key={i}
          title={`${labels[i]} — hsl(${hsl})`}
          className="h-6 w-10 rounded border border-border/60"
          style={{ backgroundColor: `hsl(${hsl})` }}
        />
      ))}
    </div>
  );
}
