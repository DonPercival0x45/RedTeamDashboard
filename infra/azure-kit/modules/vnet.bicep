// VNet + subnet delegated to the Container Apps environment.
//
// Required because Consumption-only Container Apps envs without a custom
// VNet route HTTP/HTTPS between apps but NOT TCP — our self-hosted Redis
// listens on TCP/6379 and was unreachable from sibling apps before this.
//
// Address space: 10.10.0.0/16. The CAE subnet is /23 (Consumption minimum).
// One subnet is enough; no NAT / firewall in the MVP path.

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: '${namePrefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [ '10.10.0.0/16' ]
    }
    subnets: [
      {
        name: 'cae'
        properties: {
          addressPrefix: '10.10.0.0/23'
          delegations: [
            {
              name: 'cae-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output caeSubnetId string = vnet.properties.subnets[0].id
