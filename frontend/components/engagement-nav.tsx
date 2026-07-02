"use client";

import {
  Activity,
  CalendarDays,
  DollarSign,
  FileText,
  ListChecks,
  MessageSquare,
  Network,
  Target,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

export type EngagementView =
  | "findings"
  | "entities"
  | "observations"
  | "report"
  | "costs"
  | "scope"
  | "status"
  | "contributions"
  | "tools";

const ITEMS: { view: EngagementView; label: string; Icon: LucideIcon }[] = [
  { view: "findings", label: "Findings", Icon: ListChecks },
  { view: "entities", label: "Entities", Icon: Network },
  { view: "scope", label: "Scope", Icon: Target },
  { view: "status", label: "Status", Icon: Activity },
  { view: "tools", label: "Tools", Icon: Wrench },
  { view: "contributions", label: "Contributions", Icon: CalendarDays },
  { view: "report", label: "Report", Icon: FileText },
  { view: "observations", label: "Observations", Icon: MessageSquare },
  { view: "costs", label: "Costs", Icon: DollarSign },
];

// Left rail for the engagement workspace. Selecting an item swaps the whole
// content pane (page-level), per the CHARTER's left-nav direction. The active
// item carries the single ember accent.
//
// v1.0.0(4b): onHover is called on pointerenter / focus for each nav item.
// The parent warms the react-query cache for that view so the click paints
// from cache with no loading spinner.
export function EngagementNav({
  active,
  onSelect,
  onHover,
}: {
  active: EngagementView;
  onSelect: (view: EngagementView) => void;
  onHover?: (view: EngagementView) => void;
}) {
  return (
    <nav className="w-44 shrink-0">
      <ul className="sticky top-20 space-y-1">
        {ITEMS.map(({ view, label, Icon }) => {
          const selected = active === view;
          return (
            <li key={view}>
              <button
                type="button"
                onClick={() => onSelect(view)}
                onPointerEnter={
                  onHover && !selected ? () => onHover(view) : undefined
                }
                onFocus={
                  onHover && !selected ? () => onHover(view) : undefined
                }
                aria-current={selected ? "page" : undefined}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-md border-l-2 px-3 py-2 text-sm transition-colors",
                  selected
                    ? "border-critical bg-secondary/60 text-foreground"
                    : "border-transparent text-muted-foreground hover:bg-secondary/40 hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
