// Container Apps managed environment.
//
// Split out from containerapps.bicep so the self-hosted Redis Container App
// can share the same environment (cross-app internal DNS works only within
// one env). Order: this module deploys first; redis.bicep + containerapps.bicep
// both reference its envId.

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object

param logAnalyticsCustomerId string
@secure()
param logAnalyticsPrimarySharedKey string

// VNet-integrated subnet enables internal TCP between apps in the env
// (required for the self-hosted Redis Container App).
param infrastructureSubnetId string

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
    vnetConfiguration: {
      infrastructureSubnetId: infrastructureSubnetId
      internal: false
    }
    zoneRedundant: false
  }
}

output id string = env.id
output name string = env.name
output defaultDomain string = env.properties.defaultDomain
