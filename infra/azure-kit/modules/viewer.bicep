// Azure Static Web App that hosts the RTD viewer for this tenant.
//
// The viewer is a pure Next.js static export (HTML+JS, no server). End
// users land at the SWA's default URL, sign in with Entra ID via MSAL.js
// (app-level auth — no SWA-level auth block), then talk to the backend
// over HTTPS with their Bearer token.
//
// SKU: Standard tier. Bumped from Free 2026-06-29 after Nasir + Kendall
// discussion — Standard unlocks ``networking.allowedIpRanges`` in
// staticwebapp.config.json so we can pin the viewer to specific IPs.
// MSAL.js stays as the only auth layer; no SWA-level ``auth`` block is
// configured (we don't need a second sign-in prompt).

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object

resource swa 'Microsoft.Web/staticSites@2024-04-01' = {
  name: '${namePrefix}-viewer'
  // Static Web Apps is regional, but only some regions host it. centralus
  // works; we accept the parent module's `location` and let it fail loudly
  // if the operator chose a region SWA doesn't support.
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    // No git repo wired up — the kit's install.sh pushes the prebuilt
    // bundle to this site via the SWA deployment token after Bicep
    // returns. Skipping repository* params here means "manual deploy".
    provider: 'None'
    stagingEnvironmentPolicy: 'Disabled'
    allowConfigFileUpdates: true
  }
}

output id string = swa.id
output name string = swa.name
// The hostname includes a hash, e.g. `polite-river-12345abc.6.azurestaticapps.net`.
// install.sh prints this so the operator can hand it out + bookmark it.
output hostName string = swa.properties.defaultHostname
output url string = 'https://${swa.properties.defaultHostname}'
