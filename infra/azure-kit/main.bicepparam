// Example parameters for `az deployment sub create` against main.bicep.
//
// The installer (scripts/install.sh) fills these in interactively and runs
// the deploy for you. Edit this file only if you want to drive the deploy
// directly with `az deployment sub create --parameters @main.bicepparam`.

using './main.bicep'

param env = 'prod'
param location = 'eastus2'

// Resource group name defaults to rtd-<env>. Uncomment to override.
// param resourceGroupName = 'rtd-prod'

param postgresAdminLogin = 'rtdadmin'

// Never commit this with a real value. The installer prompts for it and
// passes it inline via `--parameters postgresAdminPassword=$PG_PW`.
// param postgresAdminPassword = ''

// Image source — GHCR. Override `imageRepoOwner` if you forked the repo and
// publish images under your own account.
param imageRepoOwner = 'donpercival'
param imageTag = 'latest'

// Default model provider for runs that don't specify one. Per-run override
// (via the CLI / API) always wins, so this is just the floor default.
param llmProvider = 'anthropic'
param anthropicModel = 'claude-opus-4-7'

// MVP: keep public + Azure-services firewall. Flip to true once the VNet
// modules land.
param enablePrivateNetworking = false

// Origins allowed to call this tenant's backend from the browser. Append
// the central viewer's URL (e.g. "https://viewer.example.com") so the
// Phase 6 viewer can read findings/events/grants directly. Comma-separated.
param corsAllowOrigins = 'http://localhost:3001,http://127.0.0.1:3001'
