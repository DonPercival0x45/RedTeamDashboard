"use client";

import { useEffect, useState, type ComponentType } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  Crosshair,
  FileCheck2,
  KeyRound,
  ListChecks,
  PlayCircle,
  Radar,
} from "lucide-react";

const STORAGE_KEY = "rtd.quick-start.completed.v1";

type Step = {
  title: string;
  description: string;
  detail: string;
  href: string;
  action: string;
  icon: ComponentType<{ className?: string }>;
};

const STEPS: Step[] = [
  {
    title: "Add a provider key",
    description: "Connect the model used by analyst-assisted workflows.",
    detail: "Open Keys, add or import a provider key, test it, and select the models you want exposed to Configurations.",
    href: "/settings/keys",
    action: "Open Keys",
    icon: KeyRound,
  },
  {
    title: "Create an engagement",
    description: "Start the workspace without automatically launching anything.",
    detail: "Give the engagement a name, description, and dates. Saving creates the workspace; the analyst remains in control of every run.",
    href: "/new",
    action: "Create engagement",
    icon: Radar,
  },
  {
    title: "Define client scope",
    description: "Add formal targets and exclusions before running tools.",
    detail: "Use the Scope page to paste domains, IPs, CIDRs, and URLs. Prefix exclusions as documented in the importer preview.",
    href: "/",
    action: "Choose engagement",
    icon: Crosshair,
  },
  {
    title: "Run a suggested first action",
    description: "Use scope-aware quick actions or enter a focused prompt.",
    detail: "Enumeration and scanning actions are scope checked and approval gated. Start with one target so the result is easy to follow.",
    href: "/",
    action: "Choose engagement",
    icon: PlayCircle,
  },
  {
    title: "Follow the run into Findings",
    description: "Status explains what happened and whether findings were produced.",
    detail: "Open the live run panel or Status tab, inspect its steps, then follow the finding count back to the Findings table.",
    href: "/",
    action: "Open dashboard",
    icon: ListChecks,
  },
  {
    title: "Review, validate, and report",
    description: "Turn collected evidence into the analyst-controlled deliverable.",
    detail: "Open a finding workbench, add narrative and evidence, validate it, and use Report when the engagement is ready.",
    href: "/",
    action: "Open dashboard",
    icon: FileCheck2,
  },
];

export default function GettingStartedPage() {
  const [completed, setCompleted] = useState<Set<number>>(new Set());

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]") as number[];
      setCompleted(new Set(saved));
    } catch {
      setCompleted(new Set());
    }
  }, []);

  function toggle(index: number) {
    setCompleted((previous) => {
      const next = new Set(previous);
      if (next.has(index)) next.delete(index); else next.add(index);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(next)));
      return next;
    });
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Quick Start</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Follow the engagement feedback loop: scope → run → findings → analyst review → report.
          Checkmarks are stored in this browser and never change engagement data.
        </p>
      </div>

      <div className="rounded-lg border border-border bg-card/40 p-4">
        <div className="flex items-center justify-between gap-3 text-sm">
          <span>{completed.size} of {STEPS.length} complete</span>
          <span className="text-xs text-muted-foreground">Analysts approve active actions; automation never performs analyst-only validation.</span>
        </div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted">
          <div className="h-full bg-emerald-500 transition-all" style={{ width: `${(completed.size / STEPS.length) * 100}%` }} />
        </div>
      </div>

      <ol className="grid gap-3">
        {STEPS.map((step, index) => {
          const Icon = step.icon;
          const done = completed.has(index);
          return (
            <li key={step.title} className="rounded-lg border border-border bg-card/40 p-4">
              <div className="flex items-start gap-3">
                <button type="button" onClick={() => toggle(index)} className="mt-0.5 text-muted-foreground hover:text-foreground" aria-label={`${done ? "Mark incomplete" : "Mark complete"}: ${step.title}`}>
                  {done ? <CheckCircle2 className="h-5 w-5 text-emerald-500" /> : <span className="block h-5 w-5 rounded-full border border-border" />}
                </button>
                <Icon className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Step {index + 1}</p>
                      <h2 className="text-sm font-semibold">{step.title}</h2>
                      <p className="mt-1 text-sm">{step.description}</p>
                    </div>
                    <Link href={step.href} className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted">
                      {step.action}
                    </Link>
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">{step.detail}</p>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
