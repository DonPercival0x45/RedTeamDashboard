"use client";

// Discord Channel Connect — admin-only setup panel on /settings/feedback.
// Walks through:
//   1. Create a Discord webhook (outbound: dashboard → Discord)
//   2. Create + invite a Discord bot (inbound: Discord → dashboard)
//   3. Drop the resulting URL + token + channel id below, flip Enable.
// Re-saving sends only the fields you changed; the masked bot_token
// (e.g. "…AbCd") is rejected server-side so leaving it untouched
// preserves the stored value.

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

type DiscordConfig = {
  webhook_url?: string;
  bot_token?: string;
  channel_id?: string;
};

export function DiscordChannelConnect() {
  const [existing, setExisting] = useState<Integration | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [botToken, setBotToken] = useState("");
  const [channelId, setChannelId] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const row = await getIntegration("discord");
      setExisting(row);
      if (row) {
        const cfg = (row.config ?? {}) as DiscordConfig;
        setEnabled(row.enabled);
        setWebhookUrl(cfg.webhook_url ?? "");
        // bot_token comes back masked ("…1234"); show as placeholder so the
        // admin knows something's stored without exposing it.
        setBotToken(cfg.bot_token ?? "");
        setChannelId(cfg.channel_id ?? "");
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
      // If the bot_token field still holds the masked value (starts with
      // "…"), send it through as-is so the server keeps the existing value.
      const result = await upsertIntegration({
        type: "discord",
        enabled,
        config: {
          webhook_url: webhookUrl.trim() || undefined,
          bot_token: botToken.trim() || undefined,
          channel_id: channelId.trim() || undefined,
        },
      });
      setExisting(result);
      setStatus(
        enabled
          ? "Saved. Restart the worker to pick up bot_token / channel changes."
          : "Saved. Discord integration is disabled.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [enabled, webhookUrl, botToken, channelId]);

  const onDelete = useCallback(async () => {
    if (
      !window.confirm(
        "Remove the Discord integration? Webhook posts and the bot will stop.",
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await deleteIntegration("discord");
      setExisting(null);
      setEnabled(false);
      setWebhookUrl("");
      setBotToken("");
      setChannelId("");
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
        <CardTitle className="text-base">Discord Channel Connect</CardTitle>
        <CardDescription>
          Wire feedback to a Discord channel — push new entries out as
          notifications, and let channel messages flow back in as feedback.
          Admin-only.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <ol className="list-decimal space-y-3 pl-5 text-sm text-muted-foreground">
          <li>
            <span className="font-semibold text-foreground">
              Create a Discord channel webhook
            </span>{" "}
            (this is the dashboard → Discord direction).
            <br />
            In Discord, open the target channel → ⚙ Edit Channel → Integrations
            → Webhooks → New Webhook → Copy Webhook URL. Paste it below.
          </li>
          <li>
            <span className="font-semibold text-foreground">
              Create a Discord bot application
            </span>{" "}
            (Discord → dashboard direction).
            <br />
            Go to{" "}
            <a
              href="https://discord.com/developers/applications"
              target="_blank"
              rel="noopener noreferrer"
              className="underline decoration-dotted hover:decoration-solid"
            >
              discord.com/developers/applications
            </a>{" "}
            → New Application → Bot → Reset Token → Copy. Paste the token
            below.
          </li>
          <li>
            <span className="font-semibold text-foreground">
              Enable Message Content intent
            </span>{" "}
            on the bot.
            <br />
            Same app → Bot tab → Privileged Gateway Intents → toggle
            <em> Message Content Intent</em> → Save.
          </li>
          <li>
            <span className="font-semibold text-foreground">
              Invite the bot to your server
            </span>
            .
            <br />
            App → OAuth2 → URL Generator → check{" "}
            <code className="text-foreground">bot</code> and{" "}
            <code className="text-foreground">applications.commands</code>{" "}
            scopes; Bot Permissions: Read Messages, Add Reactions. Open the
            generated URL and pick the server + channel.
          </li>
          <li>
            <span className="font-semibold text-foreground">
              Copy the channel ID
            </span>
            .<br />
            In Discord, User Settings → Advanced → Developer Mode (on). Then
            right-click the channel → Copy Channel ID. Paste below.
          </li>
          <li>
            <span className="font-semibold text-foreground">
              Save + flip <em>Enabled</em>
            </span>
            , then restart the worker so the bot thread picks up the new
            token. Local dev:{" "}
            <code className="text-foreground">
              docker compose -f infra/docker-compose.yml restart worker
            </code>
            .
          </li>
        </ol>

        <div className="space-y-3 border-t border-border pt-4">
          <div>
            <Label htmlFor="d-webhook" className="text-xs">
              Webhook URL
            </Label>
            <Input
              id="d-webhook"
              value={webhookUrl}
              onChange={(e) => setWebhookUrl(e.target.value)}
              placeholder="https://discord.com/api/webhooks/…"
              disabled={busy}
              className="mt-1 font-mono"
            />
          </div>
          <div>
            <Label htmlFor="d-token" className="text-xs">
              Bot token{" "}
              <span className="text-muted-foreground">
                (leave masked value to keep the stored one)
              </span>
            </Label>
            <Input
              id="d-token"
              type="password"
              autoComplete="off"
              value={botToken}
              onChange={(e) => setBotToken(e.target.value)}
              placeholder="MTAxxxxx.GxYzAB.…"
              disabled={busy}
              className="mt-1 font-mono"
            />
          </div>
          <div>
            <Label htmlFor="d-channel" className="text-xs">
              Channel ID
            </Label>
            <Input
              id="d-channel"
              value={channelId}
              onChange={(e) => setChannelId(e.target.value)}
              placeholder="1234567890123456789"
              disabled={busy}
              className="mt-1 font-mono"
            />
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
              Enabled — when off, no Discord notifications are sent and the
              bot doesn't connect.
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
