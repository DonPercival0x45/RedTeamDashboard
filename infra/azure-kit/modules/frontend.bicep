// v1.0.0: Azure Container App running the Next.js frontend in Node runtime.
//
// Sits next to the existing backend Container App in the same environment
// (same VNet, same env FQDN suffix). Serves SSR + client on port 3000. No
// Key Vault secrets — every runtime value is either public (API base URL,
// Entra tenant/client IDs) or resolved on the client (MSAL access token).
//
// v1.28.0: IP allowlist moved off the ingress and onto an NSG at the
// container-apps subnet (see modules/vnet.bicep). One control plane
// covers frontend + backend + MCP. `ipSecurityRestrictions` is no
// longer set here — install.sh's post-deploy step actively clears any
// residue left over from pre-v1.28 installs.

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object

param environmentId string

@description('Full image ref, e.g. `ghcr.io/donpercival0x45/rtd-viewer:1.0.0`. The Next.js standalone runtime — same image the release workflow already builds. No build-time env is baked in; every runtime value comes from the env vars below.')
param frontendImage string

@description('Base URL of the backend Container App (e.g. `https://rtd-5qprod-app.<hash>.centralus.azurecontainerapps.io`). The Node server reads this at request time and injects into <head> as window.__RTD_CONFIG__.')
param apiBaseUrl string

@description('Entra tenant + app (client) id for analyst SSO. Blank → dev fallback identity. Not secret — MSAL.js needs both in the browser.')
param entraTenantId string = ''
param entraClientId string = ''
param entraApiScope string = ''

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-frontend'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: environmentId
    configuration: {
      ingress: {
        external: true
        targetPort: 3000
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'frontend'
          image: frontendImage
          command: [ 'node', 'server.js' ]
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'NODE_ENV', value: 'production' }
            { name: 'NEXT_TELEMETRY_DISABLED', value: '1' }
            // Runtime config (v1.0.0). app/layout.tsx reads these on the
            // server per-request and inlines them into the SSR HTML as
            // window.__RTD_CONFIG__. Client MSAL boots against the
            // injected object.
            { name: 'RTD_API_BASE_URL', value: apiBaseUrl }
            { name: 'RTD_ENTRA_TENANT_ID', value: entraTenantId }
            { name: 'RTD_ENTRA_CLIENT_ID', value: entraClientId }
            { name: 'RTD_ENTRA_API_SCOPE', value: entraApiScope }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/', port: 3000 }
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      // Single replica keeps costs low. Bump later if we ever need HA.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

output appFqdn string = app.properties.configuration.ingress.fqdn
output appName string = app.name
output url string = 'https://${app.properties.configuration.ingress.fqdn}'
