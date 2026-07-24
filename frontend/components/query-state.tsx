"use client";

import { AlertCircle, Loader2, RefreshCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function queryErrorMessage(
  error: unknown,
  fallback = "The request failed.",
): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  if (typeof error === "string" && error.trim()) return error;
  return error ? String(error) : fallback;
}

export function QueryState({
  isLoading,
  error,
  hasData = false,
  loadingLabel = "Loading…",
  errorLabel = "Could not load this data.",
  onRetry,
  isRetrying = false,
  compact = false,
}: {
  isLoading: boolean;
  error: unknown;
  hasData?: boolean;
  loadingLabel?: string;
  errorLabel?: string;
  onRetry?: () => void;
  isRetrying?: boolean;
  compact?: boolean;
}) {
  if (isLoading && !hasData) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        <span>{loadingLabel}</span>
      </div>
    );
  }
  if (!error) return null;

  const detail = queryErrorMessage(error, errorLabel);
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 text-destructive",
        compact ? "px-3 py-2 text-xs" : "p-4 text-sm",
      )}
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{hasData ? "Refresh failed; showing cached data." : errorLabel}</p>
        {detail !== errorLabel && <p className="mt-0.5 break-words opacity-90">{detail}</p>}
      </div>
      {onRetry && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onRetry}
          disabled={isRetrying}
          className="shrink-0"
        >
          <RefreshCcw className={cn("mr-1.5 h-3.5 w-3.5", isRetrying && "animate-spin")} />
          Retry
        </Button>
      )}
    </div>
  );
}
