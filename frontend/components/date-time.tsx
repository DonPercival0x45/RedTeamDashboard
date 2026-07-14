"use client";

import { cn } from "@/lib/utils";

export function DateTime({
  value,
  className,
  dateOnly = false,
}: {
  value: string | null | undefined;
  className?: string;
  dateOnly?: boolean;
}) {
  if (!value) return <span className={className}>—</span>;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return <span className={className}>—</span>;
  const options: Intl.DateTimeFormatOptions = dateOnly
    ? { year: "numeric", month: "short", day: "numeric" }
    : { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" };
  const local = date.toLocaleString(undefined, options);
  const utc = new Intl.DateTimeFormat(undefined, { ...options, timeZone: "UTC" }).format(date);
  return (
    <span className={cn("whitespace-nowrap", className)} title={date.toISOString()}>
      <span className="rtd-time-local" suppressHydrationWarning>{local}</span>
      <span className="rtd-time-utc" suppressHydrationWarning>{utc} UTC</span>
    </span>
  );
}
