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

param logAnalyticsCustomerId string
@secure()
param logAnalyticsPrimarySharedKey string

param keyVaultName string
param keyVaultId string

// Full image refs, e.g. `ghcr.io/donpercival/rtd-backend:0.1.0`.
param backendImage string
param workerImage string

param anthropicModel string = 'claude-opus-4-7'
@allowed([ 'anthropic', 'openai', 'azure' ])
param llmProvider string = 'anthropic'

// ---------------------------------------------------------------------------
// Managed environment
// ---------------------------------------------------------------------------

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-cae'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsPrimarySharedKey
      }
    }
    zoneRedundant: false
  }
}

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
  { name: 'LLM_PROVIDER', value: llmProvider }
  { name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-api-key' }
  { name: 'ANTHROPIC_MODEL', value: anthropicModel }
  { name: 'OPENAI_API_KEY', secretRef: 'openai-api-key' }
  { name: 'AZURE_OPENAI_API_KEY', secretRef: 'azure-openai-api-key' }
  { name: 'AZURE_OPENAI_ENDPOINT', secretRef: 'azure-openai-endpoint' }
  { name: 'AZURE_OPENAI_DEPLOYMENT', secretRef: 'azure-openai-deployment' }
  { name: 'AZURE_OPENAI_API_VERSION', value: '2024-08-01-preview' }
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
    environmentId: env.id
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
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 30
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
    environmentId: env.id
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
                addressFromEnv: 'REDIS_URL'
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

output environmentId string = env.id
output backendFqdn string = backend.properties.configuration.ingress.fqdn
output backendName string = backend.name
output workerName string = worker.name
