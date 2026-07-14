// v1.33.3 shared loader — analyst-specified pushup-pillars + bouncing ball.
// CSS lives in app/globals.css under `.loader`; this file is the React
// wrapper + a common overlay used when a container needs to display the
// loader centered over its own bounds while an async action is pending.

import { cn } from "@/lib/utils";
import type { CSSProperties } from "react";

type LoaderProps = {
  // Optional pixel value for the loader unit. Default 1.75 renders ~131x175.
  // Drop to ~0.6 for compact inline placements (~45x60).
  size?: number;
  className?: string;
  // Accessible label for screen readers. Callers should pass a task-specific
  // string ("Validating finding", "Sending message") so SR users get context.
  label?: string;
};

export function Loader({ size, className, label = "Loading" }: LoaderProps) {
  const style =
    typeof size === "number"
      ? ({ ["--loader-size" as string]: `${size}px` } as CSSProperties)
      : undefined;
  return (
    <div
      className={cn("loader", className)}
      style={style}
      role="status"
      aria-live="polite"
      aria-label={label}
    >
      <span className="sr-only">{label}…</span>
    </div>
  );
}

// Positions the loader centered over a `relative` parent while `show` is
// true, with a subtle backdrop that blocks pointer events on the wrapped
// UI. Use inside a `relative`-positioned container.
export function LoaderOverlay({
  show,
  size,
  label,
  className,
}: {
  show: boolean;
  size?: number;
  label?: string;
  className?: string;
}) {
  if (!show) return null;
  return (
    <div
      className={cn(
        "absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-background/70 backdrop-blur-sm",
        className,
      )}
      aria-hidden={false}
    >
      <Loader size={size} label={label} />
    </div>
  );
}
