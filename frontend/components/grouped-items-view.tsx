// v1.34.0 shared items renderer — replaces the wide-table view for tool
// findings (subfinder, httpx, nmap, burp, nessus items[]). Auto-detects a
// primary value column + an optional low-cardinality group-by column so
// the display reads as "N subdomains from crtsh, M from hackertarget"
// rather than a spreadsheet.
//
// Used from:
//   - findings-view.tsx    → slide-over GroupedItemsPanel
//   - finding-pane-client  → always-visible items block above workbench

"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Layers, Maximize2, Minimize2 } from "lucide-react";

type Item = Record<string, unknown>;

// Columns we hide because they're internal / not analyst-facing.
const HIDDEN_COLUMNS = new Set([
  "first_seen_at",
  "last_seen_at",
  "created_at",
  "updated_at",
]);

// Preferred order for primary column detection (the "main value" of each
// item). First match wins.
const PRIMARY_CANDIDATES = [
  "subdomain",
  "url",
  "host",
  "hostname",
  "target",
  "domain",
  "name",
  "path",
  "title",
];

function isPrimitive(v: unknown): v is string | number | boolean {
  return typeof v === "string" || typeof v === "number" || typeof v === "boolean";
}

function toDisplay(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function collectColumns(items: Item[]): string[] {
  const seen = new Set<string>();
  const order: string[] = [];
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    for (const k of Object.keys(item)) {
      if (HIDDEN_COLUMNS.has(k)) continue;
      if (seen.has(k)) continue;
      seen.add(k);
      order.push(k);
    }
  }
  return order;
}

// Detect two special columns:
//   primary   — the "main value" each item is about (subdomain, url, ...)
//   groupBy   — a low-cardinality column to bucket by (source, service,
//               status_code, ...). Null if no column has a useful split.
function detectStructure(items: Item[], columns: string[]): {
  primary: string | null;
  groupBy: string | null;
  extras: string[];
} {
  // Primary: preferred name that exists, else first column where every
  // value is a distinct primitive string.
  let primary: string | null = null;
  for (const name of PRIMARY_CANDIDATES) {
    if (columns.includes(name)) {
      primary = name;
      break;
    }
  }
  if (!primary) {
    for (const col of columns) {
      const values = new Set<string>();
      let stringCount = 0;
      for (const item of items) {
        const v = item[col];
        if (typeof v === "string") {
          stringCount++;
          values.add(v);
        }
      }
      if (
        stringCount === items.length &&
        values.size >= Math.max(2, Math.floor(items.length * 0.5))
      ) {
        primary = col;
        break;
      }
    }
  }

  // Group-by: any remaining primitive column with 2-10 distinct values
  // and at least 60% coverage. Skips the primary.
  let groupBy: string | null = null;
  for (const col of columns) {
    if (col === primary) continue;
    const values = new Set<string>();
    let coverage = 0;
    for (const item of items) {
      const v = item[col];
      if (isPrimitive(v) && String(v).trim() !== "") {
        coverage++;
        values.add(String(v));
      }
    }
    if (
      values.size >= 2 &&
      values.size <= 10 &&
      coverage >= Math.max(2, Math.floor(items.length * 0.6))
    ) {
      groupBy = col;
      break;
    }
  }

  const extras = columns.filter((c) => c !== primary && c !== groupBy);
  return { primary, groupBy, extras };
}

// One item rendered as a stacked block: primary value on the first
// line, then a labelled row per extras field. Long values wrap
// naturally so the card grows downward instead of overflowing the
// container (matters for Nessus items where description/solution can
// be paragraph-length).
function ItemRow({ item, primary, extras }: {
  item: Item;
  primary: string | null;
  extras: string[];
}) {
  const primaryValue = primary ? toDisplay(item[primary]) : null;
  const extraPairs = extras
    .map((k) => [k, item[k]] as const)
    .filter(([, v]) => v !== null && v !== undefined && v !== "");

  return (
    <li className="flex min-w-0 flex-col gap-1 overflow-hidden rounded border border-border/60 bg-background/60 px-2 py-1.5">
      {primaryValue !== null && (
        <span
          className="break-words font-mono text-xs text-foreground"
          title={primaryValue}
        >
          {primaryValue}
        </span>
      )}
      {extraPairs.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
          {extraPairs.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="whitespace-nowrap text-muted-foreground/70">
                {k}:
              </dt>
              <dd className="min-w-0 break-words font-mono">
                {toDisplay(v)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

function ItemsList({
  items,
  primary,
  extras,
}: {
  items: Item[];
  primary: string | null;
  extras: string[];
}) {
  return (
    <ul className="space-y-1">
      {items.map((item, idx) => (
        <ItemRow key={idx} item={item} primary={primary} extras={extras} />
      ))}
    </ul>
  );
}

function GroupSection({
  label,
  items,
  primary,
  extras,
  open,
  onToggle,
}: {
  label: string;
  items: Item[];
  primary: string | null;
  extras: string[];
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <section className="rounded-md border border-border/70 bg-card/40">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted/40"
        aria-expanded={open}
      >
        <span className="flex items-center gap-1.5">
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
          )}
          <span className="font-medium">{label}</span>
          <span className="text-[10px] text-muted-foreground">
            ({items.length})
          </span>
        </span>
      </button>
      {open && (
        <div className="border-t border-border/60 p-2">
          <ItemsList items={items} primary={primary} extras={extras} />
        </div>
      )}
    </section>
  );
}

export function GroupedItemsView({
  items,
  headerLabel = "Items",
  headerNote,
  maxHeight = "70vh",
  // When true, every group starts collapsed (analyst opens what they
  // need). When false (default), the first / largest group opens so
  // the panel isn't a wall of dropdowns on first render.
  defaultCollapsed = false,
}: {
  items: Item[];
  headerLabel?: string;
  headerNote?: string;
  maxHeight?: string;
  defaultCollapsed?: boolean;
}) {
  const columns = useMemo(() => collectColumns(items), [items]);
  const { primary, groupBy, extras } = useMemo(
    () => detectStructure(items, columns),
    [items, columns],
  );

  const groups = useMemo(() => {
    if (!groupBy) return null;
    const bucket = new Map<string, Item[]>();
    for (const item of items) {
      const v = item[groupBy];
      const key = v === null || v === undefined || v === "" ? "(unknown)" : String(v);
      const list = bucket.get(key);
      if (list) list.push(item);
      else bucket.set(key, [item]);
    }
    return Array.from(bucket.entries()).sort((a, b) => b[1].length - a[1].length);
  }, [items, groupBy]);

  // Per-group open state, keyed by label. Recomputed whenever the
  // group set changes (e.g. finding switches). Preserves user toggles
  // in between.
  const [openMap, setOpenMap] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (!groups) {
      setOpenMap({});
      return;
    }
    setOpenMap(() => {
      const next: Record<string, boolean> = {};
      groups.forEach(([label], idx) => {
        next[label] = defaultCollapsed ? false : idx === 0;
      });
      return next;
    });
  }, [groups, defaultCollapsed]);

  const anyOpen = groups?.some(([label]) => openMap[label]) ?? false;
  const allOpen = groups?.every(([label]) => openMap[label]) ?? false;

  function toggleAll(open: boolean) {
    if (!groups) return;
    const next: Record<string, boolean> = {};
    groups.forEach(([label]) => {
      next[label] = open;
    });
    setOpenMap(next);
  }

  if (!items.length) return null;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">
          <Layers className="h-3.5 w-3.5 text-muted-foreground" />
          {headerLabel}
          <span className="text-xs text-muted-foreground">({items.length})</span>
        </h3>
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
          {groupBy && (
            <span>
              grouped by <span className="font-mono">{groupBy}</span>
            </span>
          )}
          {groups && groups.length > 1 && (
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => toggleAll(true)}
                disabled={allOpen}
                className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 hover:bg-muted/40 disabled:opacity-40"
                aria-label="Expand all groups"
              >
                <Maximize2 className="h-3 w-3" />
                Expand all
              </button>
              <button
                type="button"
                onClick={() => toggleAll(false)}
                disabled={!anyOpen}
                className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 hover:bg-muted/40 disabled:opacity-40"
                aria-label="Collapse all groups"
              >
                <Minimize2 className="h-3 w-3" />
                Collapse all
              </button>
            </div>
          )}
        </div>
      </div>
      {headerNote && (
        <p className="text-[11px] text-muted-foreground/80">{headerNote}</p>
      )}
      <div
        className="space-y-2 overflow-x-hidden overflow-y-auto pr-1"
        style={{ maxHeight }}
      >
        {groups ? (
          groups.map(([label, subItems]) => (
            <GroupSection
              key={label}
              label={label}
              items={subItems}
              primary={primary}
              extras={extras}
              open={!!openMap[label]}
              onToggle={() =>
                setOpenMap((prev) => ({ ...prev, [label]: !prev[label] }))
              }
            />
          ))
        ) : (
          <ItemsList items={items} primary={primary} extras={extras} />
        )}
      </div>
    </div>
  );
}

// Read finding.data.items[] safely. Callers pass the finding straight in.
export function extractItems(data: unknown): Item[] {
  if (!data || typeof data !== "object") return [];
  const raw = (data as { items?: unknown }).items;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (v): v is Item => v !== null && typeof v === "object" && !Array.isArray(v),
  );
}

export function hasItems(data: unknown): boolean {
  return extractItems(data).length > 0;
}

