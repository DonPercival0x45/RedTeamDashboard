"use client";

// GitHub Push Connect — admin-only setup panel on /settings/feedback.
// Mirrors DiscordChannelConnect but targets the GitHub Contents API: when
// configured + enabled, the "Push to GitHub" button on the Feedback page
// commits the rendered ROADMAP.md to {owner}/{repo}@{branch}:{path} in a
// single commit, so Claude Code can read the latest approved roadmap on
// the next session.
//
// The PAT (pat_token) is masked on read like Discord's bot_token. Leaving
// the masked value in the field on save means "keep the stored token."

import { useCallback, useEffect, useState } from "react";
import { Save, Trash2 } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  deleteIntegration,
  getIntegration,
  upsertIntegration,
} from "@/lib/api";
import type { Integration } from "@/lib/types";

type GitHubConfig = {
  pat_token?: string;
  owner?: string;
  repo?: string;
  branch?: string;
  path?: string;
};

export function GitHubPushConnect() {
  const [existing, setExisting] = useState<Integration | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [patToken, setPatToken] = useState("");
  const [owner, setOwner] = useState("");
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("main");
  const [path, setPath] = useState("docs/ROADMAP.md");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const row = await getIntegration("github_push");
      setExisting(row);
      if (row) {
        const cfg = (row.config ?? {}) as GitHubConfig;
        setEnabled(row.enabled);
        setPatToken(cfg.pat_token ?? "");
        setOwner(cfg.owner ?? "");
        setRepo(cfg.repo ?? "");
        setBranch(cfg.branch ?? "main");
        setPath(cfg.path ?? "docs/ROADMAP.md");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const onSave = useCallback(async () => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const result = await upsertIntegration({
        type: "github_push",
        enabled,
        config: {
          pat_token: patToken.trim() || undefined,
          owner: owner.trim() || undefined,
          repo: repo.trim() || undefined,
          branch: branch.trim() || undefined,
          path: path.trim() || undefined,
        },
      });
      setExisting(result);
      setStatus(
        enabled
          ? "Saved. Push to GitHub is enabled."
          : "Saved. Integration is disabled — flip Enabled to push.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [enabled, patToken, owner, repo, branch, path]);

  const onDelete = useCallback(async () => {
    if (
      !window.confirm(
        "Remove the GitHub push integration? The Push to GitHub button will stop working.",
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await deleteIntegration("github_push");
      setExisting(null);
      setEnabled(false);
      setPatToken("");
      setOwner("");
      setRepo("");
      setBranch("main");
      setPath("docs/ROADMAP.md");
      setStatus("Removed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">GitHub ROADMAP Push</CardTitle>
        <CardDescription>
          Commit the rendered ROADMAP.md (approved feedback only) to a repo on
          GitHub. Claude Code reads that file on the next session. Admin-only.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <ol className="list-decimal space-y-3 pl-5 text-sm text-muted-foreground">
          <li>
            <span className="font-semibold text-foreground">
              Create a fine-grained personal access token
            </span>
            .<br />
            GitHub →{" "}
            <a
              href="https://github.com/settings/personal-access-tokens/new"
              target="_blank"
              rel="noopener noreferrer"
              className="underline decoration-dotted hover:decoration-solid"
            >
              Settings → Developer settings → Personal access tokens → Fine-grained
            </a>
            . Resource owner = your account/org, Repository access = only{" "}
            <code className="text-foreground">{`{owner}/{repo}`}</code>,
            Repository permissions:{" "}
            <code className="text-foreground">Contents → Read and write</code>.
            Copy the token (starts with{" "}
            <code className="text-foreground">github_pat_…</code>).
          </li>
          <li>
            <span className="font-semibold text-foreground">
              Fill in the target
            </span>{" "}
            below — owner is the user or org, repo is the repo name,
            branch is what you want to commit to (usually{" "}
            <code className="text-foreground">main</code>), and path is the
            file inside the repo (default{" "}
            <code className="text-foreground">docs/ROADMAP.md</code>).
          </li>
          <li>
            <span className="font-semibold text-foreground">
              Save + flip Enabled
            </span>
            , then use the <em>Push to GitHub</em> button at the top of the
            feedback list to commit. Each push is one commit; no PR is
            opened.
          </li>
        </ol>

        <div className="space-y-3 border-t border-border pt-4">
          <div>
            <Label htmlFor="gh-token" className="text-xs">
              Personal access token{" "}
              <span className="text-muted-foreground">
                (leave masked value to keep the stored one)
              </span>
            </Label>
            <Input
              id="gh-token"
              type="password"
              autoComplete="off"
              value={patToken}
              onChange={(e) => setPatToken(e.target.value)}
              placeholder="github_pat_…"
              disabled={busy}
              className="mt-1 font-mono"
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="gh-owner" className="text-xs">
                Owner
              </Label>
              <Input
                id="gh-owner"
                value={owner}
                onChange={(e) => setOwner(e.target.value)}
                placeholder="DonPercival0x45"
                disabled={busy}
                className="mt-1 font-mono"
              />
            </div>
            <div>
              <Label htmlFor="gh-repo" className="text-xs">
                Repository
              </Label>
              <Input
                id="gh-repo"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder="RedTeamDashboard"
                disabled={busy}
                className="mt-1 font-mono"
              />
            </div>
            <div>
              <Label htmlFor="gh-branch" className="text-xs">
                Branch
              </Label>
              <Input
                id="gh-branch"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                placeholder="main"
                disabled={busy}
                className="mt-1 font-mono"
              />
            </div>
            <div>
              <Label htmlFor="gh-path" className="text-xs">
                Path in repo
              </Label>
              <Input
                id="gh-path"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="docs/ROADMAP.md"
                disabled={busy}
                className="mt-1 font-mono"
              />
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              disabled={busy}
              className="h-4 w-4 rounded border-border"
            />
            <span>
              Enabled — when off, the Push to GitHub button returns a 400.
            </span>
          </label>

          {error && <p className="text-sm text-critical">{error}</p>}
          {status && <p className="text-sm text-muted-foreground">{status}</p>}

          <div className="flex items-center justify-between">
            <div className="text-xs text-muted-foreground">
              {existing
                ? `Last updated ${new Date(existing.updated_at).toLocaleString()}`
                : "Not configured yet."}
            </div>
            <div className="flex gap-2">
              {existing && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={onDelete}
                  disabled={busy}
                  className="text-muted-foreground hover:text-critical"
                >
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                  Remove
                </Button>
              )}
              <Button type="button" onClick={onSave} disabled={busy}>
                <Save className="mr-1.5 h-3.5 w-3.5" />
                {busy ? "Saving…" : "Save"}
              </Button>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
