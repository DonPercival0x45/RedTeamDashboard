// Container Apps Environment + two apps (backend, worker).
//
// The viewer is NOT deployed here — it's a central instance the operator
// points at this tenant's backend via API key. (Self-hosting the viewer in
// your own tenant is also fine; pull `ghcr.io/.../rtd-viewer:<tag>` and add
// another containerApps resource.)
//
// Images come from GHCR (public). No registry credentials required.
//
// - backend:  external ingress on 8000 -> 443. Pulls KV secrets via system identity.
// - worker:   no ingress. Same image, different entrypoint. Scales 1-3 on
//             Redis Stream depth (KEDA scaler).

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object

// Managed environment is created by containerappsenv.bicep so the redis
// Container App can share it. Pass its id here.
param environmentId string

param keyVaultName string
param keyVaultId string

// Full image refs, e.g. `ghcr.io/donpercival/rtd-backend:0.1.0`.
param backendImage string
param workerImage string

// `host:port` for KEDA's redis-streams scaler (it can't parse a redis://
// URL; addressFromEnv must be plain host:port).
param redisHostPort string

param anthropicModel string = 'claude-opus-4-7'
@allowed([ 'anthropic', 'openai', 'azure' ])
param llmProvider string = 'anthropic'

@description('Comma-separated CORS allow-origins. Add the central viewer\'s URL here so the browser can call this tenant\'s backend (Phase 6).')
param corsAllowOrigins string = 'http://localhost:3001,http://127.0.0.1:3001'

// ---------------------------------------------------------------------------
// Role assignment IDs
// ---------------------------------------------------------------------------

var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

// ---------------------------------------------------------------------------
// Secret refs + env (shared by backend and worker)
// ---------------------------------------------------------------------------

var secretsFromKeyVault = [
  {
    name: 'database-url'
    keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/database-url'
    identity: 'system'
  }
  {
    name: 'redis-url'
    keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/redis-url'
    identity: 'system'
  }
  {
    name: 'anthropic-api-key'
    keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/anthropic-api-key'
    identity: 'system'
  }
  {
    name: 'openai-api-key'
    keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/openai-api-key'
    identity: 'system'
  }
  {
    name: 'azure-openai-api-key'
    keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/azure-openai-api-key'
    identity: 'system'
  }
  {
    name: 'azure-openai-endpoint'
    keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/azure-openai-endpoint'
    identity: 'system'
  }
  {
    name: 'azure-openai-deployment'
    keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/azure-openai-deployment'
    identity: 'system'
  }
]

var sharedEnv = [
  { name: 'ENV', value: 'prod' }
  { name: 'DATABASE_URL', secretRef: 'database-url' }
  { name: 'REDIS_URL', secretRef: 'redis-url' }
  // KEDA's redis-streams scaler reads this via addressFromEnv — it expects
  // `host:port`, not a URL, so we set both REDIS_URL (for app code) and
  // REDIS_HOST_PORT (for the scaler).
  { name: 'REDIS_HOST_PORT', value: redisHostPort }
  { name: 'LLM_PROVIDER', value: llmProvider }
  { name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-api-key' }
  { name: 'ANTHROPIC_MODEL', value: anthropicModel }
  { name: 'OPENAI_API_KEY', secretRef: 'openai-api-key' }
  { name: 'AZURE_OPENAI_API_KEY', secretRef: 'azure-openai-api-key' }
  { name: 'AZURE_OPENAI_ENDPOINT', secretRef: 'azure-openai-endpoint' }
  { name: 'AZURE_OPENAI_DEPLOYMENT', secretRef: 'azure-openai-deployment' }
  { name: 'AZURE_OPENAI_API_VERSION', value: '2024-08-01-preview' }
  { name: 'CORS_ALLOW_ORIGINS', value: corsAllowOrigins }
]

// ---------------------------------------------------------------------------
// Backend
// ---------------------------------------------------------------------------

resource backend 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-backend'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: environmentId
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      // No `registries` block: GHCR's public images need no auth.
      secrets: secretsFromKeyVault
    }
    template: {
      containers: [
        {
          name: 'backend'
          image: backendImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: sharedEnv
          probes: [
            {
              // Startup gives uvicorn + the DB/Redis pings time to settle
              // before liveness takes over (Container Apps' default 1s
              // timeout kills /health mid-DB-roundtrip).
              type: 'Startup'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 5
              timeoutSeconds: 5
              failureThreshold: 12
            }
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
}

resource backendKvSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, backend.id, 'KeyVaultSecretsUser')
  scope: resourceGroup()
  properties: {
    principalId: backend.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
  }
}

// ---------------------------------------------------------------------------
// Worker — no ingress, scales on Redis Stream depth
// ---------------------------------------------------------------------------

resource worker 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-worker'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: environmentId
    configuration: {
      secrets: secretsFromKeyVault
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: workerImage
          command: [ 'python', '-m', 'app.worker.main' ]
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: sharedEnv
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        rules: [
          {
            name: 'redis-stream-depth'
            custom: {
              type: 'redis-streams'
              metadata: {
                addressFromEnv: 'REDIS_HOST_PORT'
                stream: 'runs:in'
                consumerGroup: 'osint-workers'
                pendingEntriesCount: '5'
              }
            }
          }
        ]
      }
    }
  }
}

resource workerKvSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, worker.id, 'KeyVaultSecretsUser')
  scope: resourceGroup()
  properties: {
    principalId: worker.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output backendFqdn string = backend.properties.configuration.ingress.fqdn
output backendName string = backend.name
output workerName string = worker.name
