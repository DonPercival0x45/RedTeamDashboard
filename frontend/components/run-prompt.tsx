"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { startRun } from "@/lib/api";

export function RunPrompt({
  slug,
  onStarted,
}: {
  slug: string;
  onStarted?: (threadId: string) => void;
}) {
  const [prompt, setPrompt] = useState(
    "enumerate acme.com subdomains and probe what's live",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!prompt.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await startRun(slug, { prompt: prompt.trim() });
      onStarted?.(result.thread_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Start a run</CardTitle>
        <CardDescription>
          Pushes <code>run.start</code> onto the inbound stream. The worker
          picks it up; events stream into the panels below.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="prompt">Prompt</Label>
            <Textarea
              id="prompt"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={3}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={busy}>
            {busy ? "Sending…" : "Run"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
