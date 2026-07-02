# v1.0.0 cutover checklist

Human-operated sequence for the SWA → Container App cutover on 5qprod.
Runs alongside a normal `install.sh --env 5qprod` deploy.

## Before the deploy

- [ ] `az login` — token often expires between installs. Confirm with
      `az account show`.
- [ ] Pull the tag: `git pull && git checkout v1.0.0` (or `main` after
      merge).

## Deploy

- [ ] `cd infra/azure-kit && ./scripts/install.sh --env 5qprod
      --location centralus --image-tag 1.0.0 --yes`
- [ ] Watch the "Deploy complete" banner. Confirm you see BOTH URLs:
      "Viewer (SWA)" and "Viewer (v1.0.0)".

## Entra redirect URI (one-time, per-env)

The Container App has a new hostname MSAL has never seen. **Skip this
step and SSO breaks with AADSTS50011.**

- [ ] Azure Portal → **Microsoft Entra ID** → App registrations →
      `rtd-5qprod-viewer` (appId `04bb02c7-ee3b-4d25-811a-774b7496b94c`).
- [ ] Left nav → **Authentication**.
- [ ] Under **Single-page application**, click "Add URI" and paste the
      `Viewer (v1.0.0)` URL from the deploy banner. **Do not remove the
      SWA URI** — parallel week needs it.
- [ ] Click **Save** at the top.

CLI equivalent (safe: includes the existing SWA URI so it survives):

```bash
az ad app update --id 04bb02c7-ee3b-4d25-811a-774b7496b94c \
  --set spa.redirectUris="[
    'https://lemon-cliff-0fe9ff110.7.azurestaticapps.net',
    '<the v1.0.0 URL from the banner>'
  ]"
```

## Smoke test (both viewers)

- [ ] Open the **SWA URL** in one browser tab. Sign in. Confirm you land
      on the engagement list.
- [ ] Open the **v1.0.0 URL** in a second browser tab (fresh, or another
      profile). First hit → Entra "Permissions requested" consent screen
      once per user. Approve.
- [ ] On the v1.0.0 URL: navigate to an active engagement, open Findings
      → Costs → Status → back. Confirm no visible loading spinner between
      views (TanStack Query cache).
- [ ] On the v1.0.0 URL: tab away for 30 s, tab back — Status counts
      should refresh without an F5.
- [ ] Kick off a run in an engagement. Watch Status flip to Complete /
      Failed in-frame with the SSE event (no 2 s poll wait).

## Parallel week

Both viewers stay live for at least a week. Users can bounce between
them; state is server-side so both see the same engagements.

Rollback trigger: any v1.0.0 breakage → tell users to use the SWA URL.

## Hard cut (end of parallel week)

Only after 5–7 days of clean operation and no rollback triggers.

- [ ] Send the new v1.0.0 URL to all users. Update any hard-coded
      bookmarks (Notion, PDFs, docs).
- [ ] In the Entra app registration, **remove the SWA redirect URI**.
      SSO to the SWA now breaks — this is intentional.
- [ ] Delete the SWA in Azure Portal (or leave for another week and
      delete on the next install).
- [ ] After SWA delete: on the next install.sh run, remove the SWA
      references from install.sh + main.bicep + release.yml
      (publish-viewer-static job). Track as a follow-up task.

## Known operational quirks

- **First hit consent screen.** MSAL treats each SPA origin independently;
  every user sees "Permissions requested" once on first v1.0.0 URL hit.
  Normal.
- **RTD_VIEWER_ALLOWED_IPS applies to both viewers.** Bicep stamps it
  into the Container App ingress + SWA config file in one shot. No
  action required beyond the resolver precedence (flag > SWA env > shell).
- **CORS.** Backend is provisioned with BOTH viewer origins in its
  allow-list for the parallel week. After SWA delete, next install
  drops the SWA origin. No action required.
