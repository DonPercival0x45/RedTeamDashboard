// v2.0.0 shared placeholder for nav routes that don't have real
// content yet. Uses our existing theme tokens (border-border,
// bg-card, text-muted-foreground) — same look and roundness as every
// other card in the app. The ASCII art sits in a mono block so it
// renders correctly across themes and font-size preferences.

import type { ReactNode } from "react";

// Default: cat napping while dreaming of chasing fish. Used by the
// "Coming Soon" pages (Analytics / Infrastructure) — the work is far
// enough out that "the cat is asleep dreaming about it" reads as
// intended. Thought-bubble dots (. o O) trail up from the cat's head
// to a school of fish (><>) being chased.
export const ASCII_CAT_SLEEPING = String.raw`            . o O ( ><>  ><>  ><> )
       |\      _,,,---,,_
       /,\`.-'\`'    -.  ;-;;,_    z Z
      |,4-  ) )-,_..;\ (  \`'-'
     '---''(_/--'  \`-'\_)`;

// Cat batting at a dangling toy — used on the Automation page since
// that section is "Almost There": the cat is awake and playing, not
// napping.
export const ASCII_CAT_PLAYING = String.raw`       /\_/\      *
      ( ^.^ )    /
       > ^ <----/
      /     \  o
     (_______)`;

export function PlaceholderPage({
  title,
  tagline,
  detail,
  art = ASCII_CAT_SLEEPING,
}: {
  title: string;
  // Big line under the title. "Almost There ......" / "Coming Soon....."
  tagline: string;
  // Optional single paragraph of context. Empty string / undefined
  // just leaves the tagline as the only prose.
  detail?: ReactNode;
  // ASCII art shown above the tagline. Defaults to the sleeping cat
  // for the "Coming Soon" pages; callers override to swap in a
  // "playing with toy" or other variant.
  art?: string;
}) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      </div>
      <section className="rounded-lg border border-border bg-card/40 p-8">
        <div className="mx-auto flex max-w-md flex-col items-center gap-4 text-center">
          <pre
            aria-hidden
            className="whitespace-pre font-mono text-xs leading-tight text-muted-foreground"
          >
            {art}
          </pre>
          <h2 className="text-xl font-semibold tracking-tight">{tagline}</h2>
          {detail && (
            <p className="text-sm text-muted-foreground">{detail}</p>
          )}
        </div>
      </section>
    </div>
  );
}
