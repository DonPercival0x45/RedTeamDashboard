"use client";

// v1.25.0 — What's New modal.
//
// Reached by clicking the version pill next to the analyst name.
// Renders the same routed page body inside a Dialog so a keyboard-only
// analyst gets ESC-to-close + focus trap for free.
import dynamic from "next/dynamic";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

const WhatsNewBody = dynamic(
  () => import("@/app/settings/whats-new/page").then((m) => m.default),
  {
    ssr: false,
    loading: () => (
      <div className="grid animate-pulse gap-3 p-4">
        <div className="h-5 w-40 rounded bg-muted" />
        <div className="h-3 w-64 rounded bg-muted" />
        <div className="mt-4 h-24 rounded bg-muted" />
      </div>
    ),
  },
);

export function WhatsNewModal({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          aria-label="What's new"
          className={cn(
            "fixed left-[50%] top-[50%] z-50 flex h-[85vh] w-[95vw] max-w-3xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-lg border border-border bg-background shadow-2xl",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
          )}
        >
          <header className="flex items-center justify-between border-b border-border px-5 py-3">
            <h2 className="text-base font-semibold">What&apos;s new</h2>
            <DialogPrimitive.Close
              aria-label="Close what's new"
              className="rounded-sm p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </DialogPrimitive.Close>
          </header>
          <div
            className="min-h-0 flex-1 overflow-auto"
            data-in-settings-modal="true"
          >
            <WhatsNewBody />
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
