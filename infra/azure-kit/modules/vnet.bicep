// Virtual network for the kit deployment.
//
// Two subnets:
//   container-apps  /23  — delegated to Microsoft.App/environments. Minimum
//                          size for a Consumption-profile Container Apps env.
//                          All kit containers egress from this subnet, giving
//                          Postgres a stable address space to allow.
//   postgres        /28  — delegated to Microsoft.DBforPostgreSQL/flexibleServers.
//                          Native VNet injection for Postgres Flexible Server
//                          (set at create time; not changeable post-deploy).
//
// v1.28.1: no NSG on the container-apps subnet. v1.28.0 attached one to
// gate external analyst IPs, but on Container Apps external envs the
// shared LB SNATs incoming traffic — the subnet NSG only sees the LB IP
// as the source, so a rule filtering by client CIDR never matches. The
// allowlist is back on per-app ingress `ipSecurityRestrictions` (see
// modules/frontend.bicep, modules/containerapps.bicep, modules/mcp_app.bicep).
// install.sh does a best-effort detach + delete of any residual
// rtd-<env>-nsg left behind by a v1.28.0 install.

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: '${namePrefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ '10.0.0.0/16' ] }
    subnets: [
      {
        name: 'container-apps'
        properties: {
          addressPrefix: '10.0.0.0/23'
          delegations: [
            {
              name: 'ca-delegation'
              properties: { serviceName: 'Microsoft.App/environments' }
            }
          ]
        }
      }
      {
        name: 'postgres'
        properties: {
          addressPrefix: '10.0.4.0/28'
          delegations: [
            {
              name: 'pg-delegation'
              properties: { serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers' }
            }
          ]
        }
      }
    ]
  }
}

// Private DNS zone for Postgres Flexible Server VNet injection. The zone
// name is the fixed Azure-required suffix; flexibleServers expects an
// existing zone linked to the VNet with the exact name at creation time.
// Co-located in this module because (a) it's tied to this VNet's lifecycle
// and (b) main.bicep is subscription-scoped and can't declare RG-scoped
// resources like privateDnsZones directly.
resource pgDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.postgres.database.azure.com'
  location: 'global'
  tags: tags
}

resource pgDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: pgDnsZone
  name: '${namePrefix}-pg-dns-link'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}

output vnetId string = vnet.id
output containerAppsSubnetId string = vnet.properties.subnets[0].id
output postgresSubnetId string = vnet.properties.subnets[1].id
output privateDnsZoneId string = pgDnsZone.id
