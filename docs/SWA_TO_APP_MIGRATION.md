# v0.x → v1.0.0 → v1.10.0: SWA → Container App migration (COMPLETE)

**Status:** completed 2026-07-08 with v1.10.0. The SWA was
decommissioned; the frontend Container App is the sole viewer.

## What changed

The viewer moved from Azure Static Web Apps (static Next.js export) to
Azure Container Apps (Node runtime + SSR). Unlocked:

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

## Migration timeline

- **v1.0.0 (2026-06-30 to 2026-07-06):** Container App shipped as a
  parallel viewer alongside the SWA. Both live, same backend,
  same Entra app registration.
- **v1.4.x (2026-07-06 to 2026-07-07):** Container App became the
  primary path on 5qprod (SWA static export broke at v1.0.1 due to a
  `dynamic="force-dynamic"` + `output: "export"` collision in
  `frontend/app/layout.tsx`).
- **v1.10.0 (2026-07-08):** Hard cut. Deleted `modules/viewer.bicep`,
  the `publish-viewer-static` job, `frontend/staticwebapp.config.json.template`,
  and the SWA build+deploy block in `install.sh`. Removed the
  `rtd-<env>-viewer` resource.

## Where things live now

- Viewer runtime: `rtd-<env>-frontend` Container App (Node, port 3000)
- Env vars (`RTD_API_BASE_URL` / `RTD_ENTRA_*`): frontend Container App
  container env — set by Bicep on every deploy
- IP allowlist: frontend Container App ingress
  `properties.configuration.ingress.ipSecurityRestrictions[]`
- Resolve chain: see `CLAUDE.md` § "Viewer: frontend Container App +
  IP allowlist"

## Rollback (historical — no longer possible)

Rollback to the SWA was the v1.0.0-week strategy while it existed. Post-
v1.10.0 the SWA resource is deleted and its release-workflow artifact
gone. To restore an SWA path you'd have to revert both the Bicep and
the release workflow to pre-v1.10.0 shape.
