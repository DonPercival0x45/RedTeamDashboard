"use client";

// v1.11.0: Settings > Tools tab banner.
// v1.12.0: source is the orchestrator FastMCP registry (subfinder,
// dns_lookup, port_scan, …) instead of the analyst-upload DB catalog.
//
// Sits above the admin catalog on /settings/tools and shows the tools
// that ship with the backend image. Analyst-uploaded rows continue to
// render in the admin catalog below. Read-only — no manage buttons —
// so it stays useful even when the analyst isn't an admin.
//
// Shares the phase taxonomy (Enumeration / Scanning / Analyst-only)
// with the Scope-tab "Current Tools" panel so the mental model stays
// consistent across the two surfaces.

import { Sparkles } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useDefaultTools } from "@/lib/hooks";
import { groupToolsByPhase } from "@/lib/tool-phases";

export function DefaultToolsBanner() {
  const { data: tools, error } = useDefaultTools();

  if (error) {
    // Silent-ish: the admin catalog below still renders. Emit a subtle
    // hint so someone poking at it in devtools sees why the banner is
    // empty rather than assuming there's nothing seeded.
    return (
      <Card className="border-amber-500/40 bg-amber-500/5">
        <CardContent className="py-3 text-xs text-amber-800 dark:text-amber-200">
          Couldn&apos;t load default tools — the admin catalog below is
          unaffected.
        </CardContent>
      </Card>
    );
  }

  const grouped = groupToolsByPhase(tools ?? []);
  const totalDefaults = (tools ?? []).length;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Sparkles className="h-4 w-4" />
          Ships with the install
        </CardTitle>
        <CardDescription>
          Built-in orchestrator tools baked into the backend image.
          Agents dispatch these during a run; analysts can call them
          manually via the Scope tab. Analyst-uploaded tools land in the
          catalog below.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {totalDefaults === 0 && (
          <p className="text-xs text-muted-foreground">
            No built-in tools registered on this backend image.
          </p>
        )}
        {grouped.map(({ phase, tools: phaseTools }) => {
          if (phaseTools.length === 0) return null;
          return (
            <div key={phase.key}>
              <div className="mb-1.5 flex items-baseline justify-between">
                <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {phase.label}
                </div>
                <div className="text-[10px] text-muted-foreground/70">
                  {phase.hint}
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {phaseTools.map((t) => (
                  <span
                    key={t.name}
                    title={t.description || undefined}
                    className="rounded-md border border-border/60 bg-background px-2 py-1 text-xs"
                  >
                    <span className="font-medium">{t.name}</span>
                    <span className="ml-1.5 text-[10px] text-muted-foreground/70">
                      {t.phase} · {t.risk}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
