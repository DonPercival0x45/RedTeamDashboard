// v1.9.0: font-size registry — pure data, importable from server code.
//
// Split from ``lib/font-size.ts`` (which exports the ``useFontSize``
// hook and is client-only) so the server-rendered layout's
// pre-hydration snippet can enumerate this registry synchronously.
// Same pattern as ``lib/themes.ts`` / ``lib/theme.ts``.

export type FontSizeId = "small" | "medium" | "large" | "xlarge";

export interface FontSizeMeta {
  id: FontSizeId;
  label: string;
  description: string;
  // The root font-size we stamp on <html>. Tailwind's default is 16px;
  // small drops one step, large/xlarge scale up for accessibility.
  cssValue: string;
}

export const FONT_SIZES: FontSizeMeta[] = [
  {
    id: "small",
    label: "Small",
    description: "Denser — fits more findings on screen at once.",
    cssValue: "14px",
  },
  {
    id: "medium",
    label: "Medium",
    description: "Default — matches the browser standard.",
    cssValue: "16px",
  },
  {
    id: "large",
    label: "Large",
    description: "Easier reading — good for long report reviews.",
    cssValue: "18px",
  },
  {
    id: "xlarge",
    label: "Extra Large",
    description: "Accessibility-first — maximum readability.",
    cssValue: "20px",
  },
];

export const DEFAULT_FONT_SIZE: FontSizeId = "medium";
export const FONT_SIZE_STORAGE_KEY = "rtd-font-size-v1";
export const FONT_SIZE_IDS: FontSizeId[] = FONT_SIZES.map((s) => s.id);

export function isFontSizeId(v: unknown): v is FontSizeId {
  return typeof v === "string" && FONT_SIZE_IDS.includes(v as FontSizeId);
}

export function fontSizeCss(id: FontSizeId): string {
  return (
    FONT_SIZES.find((s) => s.id === id)?.cssValue ??
    FONT_SIZES.find((s) => s.id === DEFAULT_FONT_SIZE)!.cssValue
  );
}
