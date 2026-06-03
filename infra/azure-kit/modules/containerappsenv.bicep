// Container Apps managed environment.
//
// Plain Consumption-only env — no VNet integration. With backend+worker+redis
// colocated in a single Container App (siblings sharing localhost), there is
// no cross-app internal TCP that would have needed VNet routing.

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object

param logAnalyticsCustomerId string
@secure()
param logAnalyticsPrimarySharedKey string

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

output id string = env.id
output name string = env.name
output defaultDomain string = env.properties.defaultDomain
