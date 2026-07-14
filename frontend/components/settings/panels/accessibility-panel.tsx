"use client";

// v1.25.0 — Accessibility preferences panel.
//
// Shared body between the routed /settings/accessibility page and the
// Settings modal. Toggles land in localStorage via useA11yPreferences
// and take effect immediately: reduced motion via CSS, colorblind
// severity via palette swap in globals.css, screen reader hints via
// ARIA-live regions in the app shell.
import { Accessibility } from "lucide-react";
import {
  DEFAULT_A11Y,
  useA11yPreferences,
  type ColorblindPreset,
} from "@/lib/accessibility";
import { cn } from "@/lib/utils";

const CB_PRESETS: {
  key: ColorblindPreset;
  label: string;
  hint: string;
}[] = [
  { key: "none", label: "Off", hint: "Default palette." },
  {
    key: "deuteranopia",
    label: "Deuteranopia",
    hint: "Red / green ambiguous (~6% of men). Swaps severity badges to the Okabe-Ito palette.",
  },
  {
    key: "protanopia",
    label: "Protanopia",
    hint: "Red / green ambiguous (~2% of men). Same swap as deuteranopia.",
  },
  {
    key: "tritanopia",
    label: "Tritanopia",
    hint: "Blue / yellow ambiguous (~0.01%). Uses a red / purple / teal alternate.",
  },
];

export function AccessibilityPanel({ inModal = false }: { inModal?: boolean }) {
  const { prefs, set, reset } = useA11yPreferences();
  const changed =
    prefs.reducedMotion !== DEFAULT_A11Y.reducedMotion ||
    prefs.colorblindSeverity !== DEFAULT_A11Y.colorblindSeverity ||
    prefs.screenReaderHints !== DEFAULT_A11Y.screenReaderHints ||
    prefs.timeDisplay !== DEFAULT_A11Y.timeDisplay;

  return (
    <div className={cn("space-y-6", inModal ? "" : "")}>
      {!inModal && (
        <div>
          <h1 className="mt-2 flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Accessibility className="h-6 w-6" />
            Accessibility
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Preferences save to this browser and take effect immediately.
            Nothing is synced across devices.
          </p>
        </div>
      )}

      <section className="space-y-3">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">
              Reduced motion
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Suppresses non-essential animations across the dashboard —
              banners, hover fades, panel transitions. Useful for
              vestibular sensitivity or shared-terminal setups.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={prefs.reducedMotion}
            aria-label="Toggle reduced motion"
            onClick={() => set({ reducedMotion: !prefs.reducedMotion })}
            className={cn(
              "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
              prefs.reducedMotion ? "bg-primary" : "bg-muted",
            )}
          >
            <span
              className={cn(
                "inline-block h-5 w-5 transform rounded-full bg-background shadow ring-0 transition-transform",
                prefs.reducedMotion ? "translate-x-5" : "translate-x-0.5",
              )}
            />
          </button>
        </div>
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold text-foreground">
            Colorblind-friendly severity
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Swaps the red / pink / yellow / green / blue severity badges
            for a palette that stays distinguishable under the selected
            colorblindness type. Uses the Okabe-Ito palette for
            red / green types.
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {CB_PRESETS.map((preset) => {
            const active = prefs.colorblindSeverity === preset.key;
            return (
              <button
                key={preset.key}
                type="button"
                aria-pressed={active}
                onClick={() => set({ colorblindSeverity: preset.key })}
                className={cn(
                  "rounded border px-3 py-2 text-left text-sm transition-colors",
                  active
                    ? "border-primary bg-primary/10 text-foreground"
                    : "border-border hover:border-foreground/40",
                )}
              >
                <div className="font-medium">{preset.label}</div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {preset.hint}
                </div>
              </button>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold text-foreground">Timestamp display</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Database timestamps remain canonical UTC. Choose whether the browser renders them in your local time zone or explicitly in UTC.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {(["local", "utc"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              aria-pressed={prefs.timeDisplay === mode}
              onClick={() => set({ timeDisplay: mode })}
              className={cn(
                "rounded border px-3 py-2 text-left text-sm transition-colors",
                prefs.timeDisplay === mode
                  ? "border-primary bg-primary/10 text-foreground"
                  : "border-border hover:border-foreground/40",
              )}
            >
              <span className="font-medium">{mode === "local" ? "Local time" : "UTC"}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {mode === "local" ? "Use this device’s time zone." : "Show a UTC suffix everywhere."}
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">
              Screen reader hints
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Enables an ARIA-live region that announces status
              transitions (agent finished, key stored, run completed) to
              assistive tech. Silent for sighted users.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={prefs.screenReaderHints}
            aria-label="Toggle screen reader hints"
            onClick={() =>
              set({ screenReaderHints: !prefs.screenReaderHints })
            }
            className={cn(
              "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
              prefs.screenReaderHints ? "bg-primary" : "bg-muted",
            )}
          >
            <span
              className={cn(
                "inline-block h-5 w-5 transform rounded-full bg-background shadow ring-0 transition-transform",
                prefs.screenReaderHints ? "translate-x-5" : "translate-x-0.5",
              )}
            />
          </button>
        </div>
      </section>

      {changed && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={reset}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Reset to defaults
          </button>
        </div>
      )}
    </div>
  );
}
