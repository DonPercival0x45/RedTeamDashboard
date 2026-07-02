# v0.x → v1.0.0: SWA → Container App migration

## What changed

The viewer moved from Azure Static Web Apps (static Next.js export) to
Azure Container Apps (Node runtime + SSR). This unlocks:

- **Runtime config.** One image serves any environment; no more baking
  `NEXT_PUBLIC_*` into the build. The Node server reads `RTD_*` env vars
  per request and injects them into the SSR HTML as `window.__RTD_CONFIG__`.
- **TanStack Query as the data layer.** Every fetch across the frontend is
  now on `useQuery`/`useMutation`. Focus revalidation + intelligent
  polling + prefetch-on-hover replaces the old per-page `useEffect + fetch`
  plumbing.
- **SSE-to-cache bridge.** SSE events (`finding.created`, `run.completed`,
  `run.errored`) merge directly into the query cache, so live updates
  land without waiting for the next 2 s status poll.

## What did NOT change

- The backend Container App, Postgres, Key Vault, Application Insights,
  storage, VNet, and MCP app are unchanged.
- MSAL.js is still the browser auth layer. SSO flow is identical.
- The API surface is unchanged.
- Existing engagements + findings + all persisted state carry over.

## Fresh-env deploys

The kit installs both viewers in parallel:

- **SWA** (`modules/viewer.bicep`) — Standard SKU. Kept live during the
  parallel week so operators can roll back by pointing bookmarks at the
  old URL if the Container App misbehaves.
- **Container App** (`modules/frontend.bicep`, new) — Node runtime.
  Ingress on 3000. Same env vars as the SWA (via a runtime injection
  instead of a build-time bake).

Both are provisioned by a single `./scripts/install.sh` run. Nothing
different for the operator.

## Env vars

The Container App reads these at request time. Empty `RTD_ENTRA_*` values
still boot but fall back to `analyst@localhost` dev identity.

| Var | Where set | Purpose |
| --- | --- | --- |
| `RTD_API_BASE_URL` | Bicep, from backend FQDN | Base URL for API calls |
| `RTD_ENTRA_TENANT_ID` | Bicep, from `entraTenantId` | MSAL tenant |
| `RTD_ENTRA_CLIENT_ID` | Bicep, from `entraClientId` | MSAL app registration |
| `RTD_ENTRA_API_SCOPE` | Bicep, `api://<clientId>/access_as_user` | Backend scope |

## IP allowlist

Same source of truth as the SWA: `RTD_VIEWER_ALLOWED_IPS`. Precedence
order (unchanged from v0.x):

1. `--allowed-ips` CLI flag (empty value clears the lock)
2. SWA Environment Variables blade
3. Shell env

`install.sh` reads the resolved list once and stamps it into **both** the
SWA `networking.allowedIpRanges` (staticwebapp.config.json) and the
Container App ingress `ipSecurityRestrictions`. During the parallel week
they enforce identically; after decommission the Container App is the
only enforcement point.

## Entra redirect URI

The Container App is a new SPA origin from MSAL's POV. **The Entra app
registration must have BOTH the SWA URL and the Container App URL listed
under Authentication → Single-page application → Redirect URIs.** The
kit doesn't touch the app registration; add manually. See
`docs/V1_CUTOVER_CHECKLIST.md` for the exact steps.

Failing to add the Container App URL → `AADSTS50011: redirect URI
mismatch` on first sign-in.

## Rollback

If the Container App misbehaves:

- **Bookmark rollback.** Point users at the SWA URL. Both are live during
  the parallel week; nothing else changes.
- **Full rollback.** Deploy the previous kit tag: `./scripts/install.sh
  --env <env> --image-tag <prev>`. The SWA path is unchanged from v0.x, so
  it comes back exactly as before.
