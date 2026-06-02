// Container Registry — holds backend, worker, frontend images.
// Basic SKU is the cheapest tier; sufficient for Phase 0. Bump to Standard
// when image storage > 10 GB or you need geo-replication.

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object

// ACR names must be alphanumeric only. Strip dashes.
var registryName = replace('${namePrefix}acr', '-', '')

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: registryName
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false  // pull via managed identity, not admin creds
  }
}

output id string = acr.id
output name string = acr.name
output loginServer string = acr.properties.loginServer
