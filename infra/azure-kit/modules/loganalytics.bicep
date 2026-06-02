// Log Analytics workspace — Container Apps Environment pipes stdout from
// every container here. One workspace per environment is enough; retention
// stays at the 30-day default to keep cost down.

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
  }
}

output workspaceId string = workspace.id
output customerId string = workspace.properties.customerId
@secure()
output primarySharedKey string = workspace.listKeys().primarySharedKey
