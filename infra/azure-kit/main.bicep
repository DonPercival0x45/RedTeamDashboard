// Red Team Dashboard — Deployment Kit (subscription-scoped).
//
// Provisions, in one resource group, the per-tenant backend an operator owns:
//   - VNet with two delegated subnets (Container Apps /23, Postgres /28)
//   - Private DNS zone for Postgres VNet injection
//   - Log Analytics workspace
//   - Application Insights (workspace-based)
//   - Postgres Flexible Server — VNet-injected, no public access
//   - Key Vault (RBAC mode) with seeded secrets
//   - Container Apps Environment (Consumption, VNet-integrated)
//   - One Container App with three colocated containers: backend, worker, redis
//   - Azure Static Web App hosting the viewer (gated by Entra ID)
//
// What's NOT here:
//   - Any container registry: images are public on GHCR. No auth needed.
//   - LLM API keys: placeholders in Key Vault; operator populates after deploy.
//   - Azure OpenAI resource: provision separately and populate the KV secrets
//     if using llmProvider=azure. Default is anthropic.
//   - The admin API key: installer mints it from the running backend after
//     the deploy completes and overwrites the admin-api-key placeholder.
//
// The kit is designed for the operator to run once per engagement (or once
// total, then archive engagements via the API). Teardown is a single
// `az group delete`.

targetScope = 'subscription'

@description('Short env name; becomes part of every resource name (e.g. "prod", "ops").')
param env string = 'prod'

@description('Azure region for everything.')
param location string = 'eastus2'

@description('Resource group name. Defaults to rtd-<env>.')
param resourceGroupName string = 'rtd-${env}'

@description('Postgres admin username.')
param postgresAdminLogin string = 'rtdadmin'

@description('Postgres admin password. Pass via @secure() bicepparam or CLI prompt.')
@secure()
param postgresAdminPassword string

@description('GHCR repository owner (e.g. "donpercival0x45"). The kit pulls images from ghcr.io/<owner>/rtd-{backend,worker}:<tag>.')
param imageRepoOwner string = 'donpercival0x45'

@description('Image tag for backend + worker (e.g. "0.1.0", "v0.1.0", "main"). Bump on each release.')
param imageTag string = 'latest'

@description('Default LLM provider for runs that don\'t pick one explicitly. The CLI/API can override per run.')
@allowed([ 'anthropic', 'openai', 'azure' ])
param llmProvider string = 'anthropic'

@description('Default Anthropic model when the run uses Anthropic without picking one. Per-run override wins.')
param anthropicModel string = 'claude-opus-4-7'

@description('Extra CORS allow-origins for the browser viewer. The kit auto-appends the in-tenant Static Web App URL; use this only to add other origins (e.g. a self-hosted viewer at a custom domain). Comma-separated.')
param extraCorsAllowOrigins string = 'http://localhost:3001,http://127.0.0.1:3001'

@description('Entra tenant + app (client) id for analyst SSO (from setup-entra.sh). Blank → Entra auth stays off; backend uses API keys. See docs/ENTRA_SETUP.md.')
param entraTenantId string = ''
param entraClientId string = ''

@description('v1.0.0: Comma-separated IPv4 CIDRs the frontend Container App will accept. Empty → no restriction. install.sh resolves + persists this the same way it does for the SWA (RTD_VIEWER_ALLOWED_IPS).')
param frontendAllowedIps string = ''

var namePrefix = 'rtd-${env}'
var tags = {
  app: 'red-team-dashboard'
  env: env
  managedBy: 'bicep-kit'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

// ---------------------------------------------------------------------------
// Networking — VNet + private DNS zone for Postgres
// ---------------------------------------------------------------------------

module vnet 'modules/vnet.bicep' = {
  name: 'vnet'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
  }
}

// Private DNS zone for Postgres VNet injection is now created INSIDE
// vnet.bicep — subscription-scoped main.bicep can't declare a
// resource-group-scoped resource like privateDnsZones directly. See
// modules/vnet.bicep for the zone + vnet-link definitions.

// ---------------------------------------------------------------------------
// Observability
// ---------------------------------------------------------------------------

module logs 'modules/loganalytics.bicep' = {
  name: 'logs'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
  }
}

module ai 'modules/appinsights.bicep' = {
  name: 'appinsights'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    workspaceId: logs.outputs.workspaceId
  }
}

// ---------------------------------------------------------------------------
// Data tier
// ---------------------------------------------------------------------------

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    adminLogin: postgresAdminLogin
    adminPassword: postgresAdminPassword
    delegatedSubnetId: vnet.outputs.postgresSubnetId
    privateDnsZoneId: vnet.outputs.privateDnsZoneId
  }
}

module kv 'modules/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    postgresPassword: postgresAdminPassword
    databaseUrl: postgres.outputs.sqlAlchemyUrl
  }
}

// ---------------------------------------------------------------------------
// Storage — engagement export archive (blob)
// ---------------------------------------------------------------------------

module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// Compute tier
// ---------------------------------------------------------------------------

module caenv 'modules/containerappsenv.bicep' = {
  name: 'containerappsenv'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    logAnalyticsCustomerId: logs.outputs.customerId
    logAnalyticsPrimarySharedKey: logs.outputs.primarySharedKey
    infrastructureSubnetId: vnet.outputs.containerAppsSubnetId
  }
}

module viewer 'modules/viewer.bicep' = {
  name: 'viewer'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
  }
}

var backendImage = 'ghcr.io/${imageRepoOwner}/rtd-backend:${imageTag}'
var workerImage = 'ghcr.io/${imageRepoOwner}/rtd-worker:${imageTag}'
var frontendImage = 'ghcr.io/${imageRepoOwner}/rtd-viewer:${imageTag}'

// Stage 2 — secondary MCP App with scale-to-zero. Lives in the same env
// so internal DNS just works; ingress is external so the worker can
// reach it via HTTPS the same way it reaches the colocated /mcp. The
// main App below picks up its URL via the ACA_MCP_URL env var so
// Tactical can route ``lease.requires_container=True`` runs there.
module mcpApp 'modules/mcp_app.bicep' = {
  name: 'mcpApp'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    environmentId: caenv.outputs.id
    keyVaultName: kv.outputs.name
    keyVaultId: kv.outputs.id
    backendImage: backendImage
    appInsightsConnectionString: ai.outputs.connectionString
  }
}

// v1.0.0: Compose the frontend FQDN from convention so `apps` (backend
// CORS) and `frontend` (backend URL) don't reference each other's outputs
// — that would produce a Bicep dependency cycle. Every Container App in
// a given env shares one `caenv.defaultDomain` suffix, so this
// composition is stable.
var frontendFqdn = '${namePrefix}-frontend.${caenv.outputs.defaultDomain}'
var backendFqdn = '${namePrefix}-app.${caenv.outputs.defaultDomain}'

// v1.0.0: frontend Container App (Node runtime). Runs alongside the SWA
// during the parallel week; after decommission this becomes the only
// viewer. Uses the composed backend FQDN as its RTD_API_BASE_URL.
module frontend 'modules/frontend.bicep' = {
  name: 'frontend'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    environmentId: caenv.outputs.id
    frontendImage: frontendImage
    apiBaseUrl: 'https://${backendFqdn}'
    entraTenantId: entraTenantId
    entraClientId: entraClientId
    // Backend expects `api://<clientId>/access_as_user` — build here so
    // install.sh doesn't have to.
    entraApiScope: empty(entraClientId) ? '' : 'api://${entraClientId}/access_as_user'
    allowedIps: frontendAllowedIps
  }
}

module apps 'modules/containerapps.bicep' = {
  name: 'containerapps'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    environmentId: caenv.outputs.id
    keyVaultName: kv.outputs.name
    keyVaultId: kv.outputs.id
    backendImage: backendImage
    workerImage: workerImage
    llmProvider: llmProvider
    anthropicModel: anthropicModel
    // v1.0.0: two viewer origins during the parallel week — the SWA
    // (viewer.outputs.url) and the new frontend Container App
    // (frontendFqdn, composed above). Both need CORS access to the
    // backend.
    corsAllowOrigins: '${extraCorsAllowOrigins},${viewer.outputs.url},https://${frontendFqdn}'
    entraTenantId: entraTenantId
    entraClientId: entraClientId
    appInsightsConnectionString: ai.outputs.connectionString
    storageAccountName: storage.outputs.storageAccountName
    acaMcpUrl: mcpApp.outputs.appUrl
    acaMcpAppEnabled: true
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

// Grant the container app's managed identity Storage Blob Data Contributor
// on the exports account. The role assignment lives in its own RG-scoped
// module because main.bicep is subscription-scoped — see
// modules/storage_roles.bicep for why.
module storageRoles 'modules/storage_roles.bicep' = {
  name: 'storageRoles'
  scope: rg
  params: {
    storageAccountName: storage.outputs.storageAccountName
    principalId: apps.outputs.appPrincipalId
  }
}

// v0.12.0: Tools tab sandbox runner (ACIRunner) needs Contributor on
// the RG so the backend's managed identity can spawn / delete Azure
// Container Instances per tool invocation. Scoped tight to rtd-<env>.
module aciRoles 'modules/aci_roles.bicep' = {
  name: 'aciRoles'
  scope: rg
  params: {
    principalId: apps.outputs.appPrincipalId
  }
}

output resourceGroupName string = rg.name
output appFqdn string = apps.outputs.appFqdn
output appName string = apps.outputs.appName
output keyVaultName string = kv.outputs.name
output postgresFqdn string = postgres.outputs.fqdn
output viewerName string = viewer.outputs.name
output viewerUrl string = viewer.outputs.url
// v1.0.0: frontend Container App outputs.
output frontendAppName string = frontend.outputs.appName
output frontendUrl string = frontend.outputs.url
output appInsightsName string = ai.outputs.name
output storageAccountName string = storage.outputs.storageAccountName
output mcpAppName string = mcpApp.outputs.appName
output mcpAppFqdn string = mcpApp.outputs.appFqdn
output mcpAppUrl string = mcpApp.outputs.appUrl
