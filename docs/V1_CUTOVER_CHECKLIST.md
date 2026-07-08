# v1.0.0 cutover — historical note

**Complete.** The Azure Static Web App (`rtd-<env>-viewer`) was the
original viewer. v1.0.0 introduced the frontend Container App as a
parallel viewer; users bounced between both for the parallel week.
v1.10.0 hard-cut the SWA — removed the Bicep module, install.sh build
step, and release.yml `publish-viewer-static` job, and deleted the
`rtd-5qprod-viewer` resource.

Post-v1.10.0 the frontend Container App is the only viewer path. IP
restrictions live on the ingress; Entra IDs live on the container env
vars. See `CLAUDE.md` § "Viewer: frontend Container App + IP allowlist"
for the current resolve chain.

Kept as a breadcrumb so future readers know why the release.yml `needs:`
was odd in v1.0.1 → v1.7.0 (SWA static-bundle job gated GitHub Releases
and silently failed) and why the SWA-app-settings-lookup pattern shows
up in git history.
