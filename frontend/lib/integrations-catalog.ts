// v0.9.0: provider catalog for /settings/integrations. Each entry
// declares the metadata the tab grid + setup modal need to render a
// provider tile and walk an admin through wiring it up. New providers
// land here without backend changes — the model column is a free-form
// VARCHAR.

import type { LucideIcon } from "lucide-react";
import { Globe, MessageSquare, Send, UploadCloud } from "lucide-react";

import type { IntegrationPurpose } from "@/lib/types";

export interface ProviderField {
  /** Config-JSONB key the value lands under. */
  key: string;
  label: string;
  /** Free-text vs password-style mask vs URL — drives the input element
   *  + masking on read in the form. */
  kind: "text" | "secret" | "url" | "json";
  placeholder?: string;
  /** If true, the modal refuses to Save until this field is non-empty. */
  required?: boolean;
  /** Optional inline help under the input. */
  help?: string;
}

export interface ProviderDef {
  /** Free-form slug stored in `Integration.type`. Stable; the source
   *  of truth for provider identity across releases. */
  type: string;
  label: string;
  /** Tile + modal-header icon. Lucide for built-ins; Custom uses a
   *  URL-uploaded image instead and the tile renders <img>. */
  icon: LucideIcon;
  /** One-liner shown on the tile under the label. */
  tagline: string;
  /** Default purpose pre-selected in the modal. Admin can override. */
  defaultPurpose: IntegrationPurpose;
  /** Step-by-step setup instructions rendered above the form fields.
   *  Bullet strings; the modal renders them as a numbered list. */
  setupSteps: string[];
  /** Config fields the modal renders inputs for. */
  fields: ProviderField[];
  /** When true, an admin can upload a square logo via a data URL; the
   *  uploaded image overrides the default icon on the configured tile. */
  acceptsCustomLogo?: boolean;
}

// Discord — outbound webhook + the existing inbound bot. The bot side
// only fires when a row with bot_token + channel_id exists; outbound
// notifications fire on any row with webhook_url (regardless of bot
// fields).
export const DISCORD_PROVIDER: ProviderDef = {
  type: "discord",
  label: "Discord",
  icon: MessageSquare,
  tagline: "Outbound webhook + inbound bot (one server channel).",
  defaultPurpose: "feedback",
  setupSteps: [
    "In Discord, open the target channel → ⚙ Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL. Paste it below.",
    "Optional (inbound bot): go to discord.com/developers/applications → New Application → Bot → Reset Token → Copy. Paste the token below.",
    "If you set a bot token, enable the Message Content intent and invite the bot to your server with Read Messages + Add Reactions permissions; copy the channel ID into the field below.",
    "Save + flip Enabled. Restart the worker so the bot thread picks up token / channel changes.",
  ],
  fields: [
    {
      key: "webhook_url",
      label: "Webhook URL",
      kind: "url",
      placeholder: "https://discord.com/api/webhooks/…",
      required: true,
    },
    {
      key: "bot_token",
      label: "Bot token (optional — inbound bot)",
      kind: "secret",
      placeholder: "MTAxxxxx.GxYzAB.…",
      help: "Leave the masked value to keep the stored token.",
    },
    {
      key: "channel_id",
      label: "Channel ID (required if bot token set)",
      kind: "text",
      placeholder: "1234567890123456789",
    },
  ],
};

// Teams — outbound webhook only (Adaptive Cards).
export const TEAMS_PROVIDER: ProviderDef = {
  type: "teams",
  label: "Microsoft Teams",
  icon: Send,
  tagline: "Outbound webhook only (Adaptive Cards).",
  defaultPurpose: "status_alerts",
  setupSteps: [
    "In Teams, open the channel where alerts should land → ⋯ → Manage channel → Connectors → Configure 'Incoming Webhook'.",
    "Give the webhook a name (e.g. 'RTD Alerts'), optionally upload an icon, click Create. Copy the URL it shows.",
    "Paste the webhook URL below, choose a purpose (status_alerts is the most useful for Teams), save + flip Enabled.",
  ],
  fields: [
    {
      key: "webhook_url",
      label: "Incoming Webhook URL",
      kind: "url",
      placeholder: "https://<tenant>.webhook.office.com/webhookb2/…",
      required: true,
    },
  ],
};

// GitHub-push — what was the v0.6.0 ROADMAP push integration. Carries a
// PAT for the Contents API.
export const GITHUB_PUSH_PROVIDER: ProviderDef = {
  type: "github_push",
  label: "GitHub (ROADMAP push)",
  icon: UploadCloud,
  tagline: "Commit approved feedback as ROADMAP.md.",
  defaultPurpose: "roadmap_push",
  setupSteps: [
    "Create a fine-grained PAT at github.com/settings/personal-access-tokens/new with Repository → Contents → Read+Write on the target repo.",
    "Paste the PAT, owner, repo, branch (usually main), and path (e.g. docs/ROADMAP.md) below.",
    "Save + flip Enabled. The Push to GitHub button on /settings/feedback uses this row.",
  ],
  fields: [
    {
      key: "pat_token",
      label: "Personal access token",
      kind: "secret",
      placeholder: "github_pat_…",
      required: true,
      help: "Leave the masked value to keep the stored token.",
    },
    {
      key: "owner",
      label: "Owner",
      kind: "text",
      placeholder: "your-org",
      required: true,
    },
    {
      key: "repo",
      label: "Repository",
      kind: "text",
      placeholder: "RedTeamDashboard",
      required: true,
    },
    {
      key: "branch",
      label: "Branch",
      kind: "text",
      placeholder: "main",
      required: true,
    },
    {
      key: "path",
      label: "Path in repo",
      kind: "text",
      placeholder: "docs/ROADMAP.md",
      required: true,
    },
  ],
};

// Custom — for anything not on the built-in list. Slack / PagerDuty /
// n8n / Zapier / your own internal webhook receiver all fit here.
export const CUSTOM_PROVIDER: ProviderDef = {
  type: "custom",
  label: "Custom",
  icon: Globe,
  tagline: "Any webhook + JSON template. Upload your own logo.",
  defaultPurpose: "manual",
  acceptsCustomLogo: true,
  setupSteps: [
    "Paste the webhook URL of the service you want to notify.",
    "Optional: write a JSON template using {title} / {body} / {status} / {kind} placeholders. We'll substitute them at send time. Leave blank to send our default {title, body, status, kind} payload.",
    "Optional: upload a square logo (PNG/SVG, max 64KB) so the tile shows your service's branding.",
    "Save + flip Enabled.",
  ],
  fields: [
    {
      key: "webhook_url",
      label: "Webhook URL",
      kind: "url",
      placeholder: "https://hooks.slack.com/services/…",
      required: true,
    },
    {
      key: "json_template",
      label: "JSON template (optional)",
      kind: "json",
      placeholder: '{"text":"{title} — {body}"}',
      help: "Placeholders: {title}, {body}, {status}, {kind}.",
    },
    {
      key: "api_key",
      label: "Bearer token (optional)",
      kind: "secret",
      help: "Sent as Authorization: Bearer <token> if set.",
    },
  ],
};

export const PROVIDER_CATALOG: ProviderDef[] = [
  DISCORD_PROVIDER,
  TEAMS_PROVIDER,
  GITHUB_PUSH_PROVIDER,
  CUSTOM_PROVIDER,
];

export function findProvider(type: string): ProviderDef | undefined {
  return PROVIDER_CATALOG.find((p) => p.type === type);
}

export const PURPOSE_LABELS: Record<IntegrationPurpose, string> = {
  feedback: "Feedback notifications",
  status_alerts: "Status alerts (agent/run failures)",
  roadmap_push: "ROADMAP push to a repo",
  manual: "Manual (no auto-events wired)",
};
