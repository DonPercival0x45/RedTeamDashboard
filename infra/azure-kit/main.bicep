// Red Team Dashboard — Deployment Kit (subscription-scoped).
//
// Provisions, in one resource group, the per-tenant backend an operator owns:
//   - Log Analytics workspace
//   - Postgres Flexible Server (Burstable B1ms)
//   - Container Apps Environment (shared by redis + backend + worker)
//   - Self-hosted Redis Container App (internal-only TCP ingress on 6379;
//     Azure Cache for Redis is retired for new deployments)
//   - Key Vault (RBAC mode) with seeded secrets
//   - Backend + worker Container Apps (images pulled from public GHCR)
//
// What's NOT here:
//   - The viewer: hosted centrally; the operator plugs in this deployment's
//     backend URL + an API key from the central viewer's UI.
//   - Any container registry: images are public on GHCR. No auth needed.
//   - LLM API keys: placeholders in Key Vault; operator populates after deploy.
//   - The admin API key: installer mints it from the running backend after
//     the deploy completes (so the schema exists) and overwrites the
//     `admin-api-key` placeholder secret.
//
// The kit is designed for the operator to run once per engagement (or once
// total, then archive engagements via the API). Teardown is a single
// `az group delete` — see scripts/uninstall.sh.

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

@description('GHCR repository owner (e.g. "donpercival"). The kit pulls images from ghcr.io/<owner>/rtd-{backend,worker}:<tag>.')
param imageRepoOwner string = 'donpercival'

@description('Image tag for backend + worker (e.g. "0.1.0", "v0.1.0", "main"). Bump on each release.')
param imageTag string = 'latest'

@description('Default LLM provider for runs that don\'t pick one explicitly. The CLI/API can override per run.')
@allowed([ 'anthropic', 'openai', 'azure' ])
param llmProvider string = 'anthropic'

@description('Default Anthropic model when the run uses Anthropic without picking one. Per-run override wins.')
param anthropicModel string = 'claude-opus-4-7'

@description('Reserved for the future VNet path. Today only flips public/private network flags on the data plane — it does NOT wire up VNet + private endpoints (next iteration). Leave false for the MVP.')
param enablePrivateNetworking bool = false

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

module logs 'modules/loganalytics.bicep' = {
  name: 'logs'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    adminLogin: postgresAdminLogin
    adminPassword: postgresAdminPassword
  }
}

// VNet for the Container Apps env. Plain Consumption envs without a custom
// VNet route HTTP between apps but NOT TCP — we need TCP to reach the
// self-hosted Redis Container App.
module vnet 'modules/vnet.bicep' = {
  name: 'vnet'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
  }
}

// Container Apps env is shared by the self-hosted redis + backend + worker.
// Deployed after vnet so it can drop into the delegated subnet.
module caenv 'modules/containerappsenv.bicep' = {
  name: 'containerappsenv'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    logAnalyticsCustomerId: logs.outputs.customerId
    logAnalyticsPrimarySharedKey: logs.outputs.primarySharedKey
    infrastructureSubnetId: vnet.outputs.caeSubnetId
  }
}

module redis 'modules/redis.bicep' = {
  name: 'redis'
  scope: rg
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    environmentId: caenv.outputs.id
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
    redisUrl: redis.outputs.url
  }
}

// Image refs the container apps pull from GHCR. Public; no registry creds.
var backendImage = 'ghcr.io/${imageRepoOwner}/rtd-backend:${imageTag}'
var workerImage = 'ghcr.io/${imageRepoOwner}/rtd-worker:${imageTag}'

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
    redisHostPort: redis.outputs.hostPort
    llmProvider: llmProvider
    anthropicModel: anthropicModel
  }
}

output resourceGroupName string = rg.name
output backendFqdn string = apps.outputs.backendFqdn
output backendName string = apps.outputs.backendName
output workerName string = apps.outputs.workerName
output keyVaultName string = kv.outputs.name
output postgresFqdn string = postgres.outputs.fqdn
output redisHostName string = redis.outputs.hostName
output redisInternalUrl string = redis.outputs.url
output privateNetworkingEnabled bool = enablePrivateNetworking
