"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getUserId, setUserId } from "@/lib/user";

// Phase 0 stand-in for auth: prompt for an email-style identifier on first
// visit, persist to localStorage, attach as X-User-Id on every API call.
// Drop in Entra OIDC behind this seam later.

export function UserIdGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [value, setValue] = useState("");

  useEffect(() => {
    if (getUserId()) setReady(true);
  }, []);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;
    setUserId(trimmed);
    setReady(true);
  };

  if (ready) return <>{children}</>;

  return (
    <main className="container flex min-h-[60vh] items-center justify-center">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-lg border bg-card p-6 shadow-sm"
      >
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">Identify yourself</h2>
          <p className="text-sm text-muted-foreground">
            Used as the <code>X-User-Id</code> header on every API call. Email
            address or UUID; will persist in this browser.
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="user-id">Email or UUID</Label>
          <Input
            id="user-id"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="analyst@example.com"
            autoFocus
          />
        </div>
        <Button type="submit" className="w-full">
          Continue
        </Button>
      </form>
    </main>
  );
}
