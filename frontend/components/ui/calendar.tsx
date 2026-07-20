"use client";

import * as React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface CalendarProps {
  selected?: Date;
  onSelect: (date: Date) => void;
  minDate?: Date;
  maxDate?: Date;
  className?: string;
}

const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export function Calendar({
  selected,
  onSelect,
  minDate,
  maxDate,
  className,
}: CalendarProps) {
  const anchor = React.useMemo(
    () => selected ?? new Date(),
    [selected],
  );
  const [viewMonth, setViewMonth] = React.useState<Date>(
    () => new Date(anchor.getFullYear(), anchor.getMonth(), 1),
  );

  const today = startOfDay(new Date());
  const min = minDate ? startOfDay(minDate) : undefined;
  const max = maxDate ? startOfDay(maxDate) : undefined;

  const y = viewMonth.getFullYear();
  const m = viewMonth.getMonth();
  const firstWeekday = new Date(y, m, 1).getDay();

  const cells: Date[] = [];
  for (let i = 0; i < 42; i++) {
    cells.push(new Date(y, m, 1 - firstWeekday + i));
  }

  const lastDayOfPrevMonth = new Date(y, m, 0);
  const firstDayOfNextMonth = new Date(y, m + 1, 1);
  const canGoPrev = !min || lastDayOfPrevMonth >= min;
  const canGoNext = !max || firstDayOfNextMonth <= max;

  const monthLabel = viewMonth.toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
  });

  return (
    <div
      role="dialog"
      aria-label="Choose date"
      className={cn(
        "w-[280px] rounded-md border bg-popover p-3 text-popover-foreground shadow-md",
        className,
      )}
    >
      <div className="mb-2 flex items-center justify-between">
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="h-7 w-7"
          onClick={() => setViewMonth(new Date(y, m - 1, 1))}
          disabled={!canGoPrev}
          aria-label="Previous month"
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <div className="text-sm font-medium">{monthLabel}</div>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="h-7 w-7"
          onClick={() => setViewMonth(new Date(y, m + 1, 1))}
          disabled={!canGoNext}
          aria-label="Next month"
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
      <div className="mb-1 grid grid-cols-7 gap-1 text-center text-xs text-muted-foreground">
        {WEEKDAYS.map((d) => (
          <div key={d}>{d}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells.map((d, i) => {
          const inMonth = d.getMonth() === m;
          const disabled = Boolean((min && d < min) || (max && d > max));
          const isSelected = selected != null && sameDay(d, selected);
          const isToday = sameDay(d, today);
          return (
            <button
              key={i}
              type="button"
              disabled={disabled}
              onClick={() => onSelect(startOfDay(d))}
              className={cn(
                "inline-flex h-8 w-8 items-center justify-center rounded text-sm transition-colors",
                !inMonth && "text-muted-foreground/40",
                disabled && "cursor-not-allowed opacity-40",
                !disabled &&
                  !isSelected &&
                  "hover:bg-accent hover:text-accent-foreground",
                isSelected && "bg-primary text-primary-foreground",
                !isSelected && isToday && "ring-1 ring-primary/60",
              )}
              aria-pressed={isSelected}
              aria-label={d.toDateString()}
            >
              {d.getDate()}
            </button>
          );
        })}
      </div>
    </div>
  );
}
