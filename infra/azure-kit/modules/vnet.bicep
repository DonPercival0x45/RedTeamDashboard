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
// v1.28.0: IP allowlist moved from ingress ipSecurityRestrictions to an NSG
// on the container-apps subnet — one control plane covers the whole
// environment (frontend + backend + MCP). Two rules on top of the default
// deny-all-inbound at 65500: AzureLoadBalancer at 100 (mandatory — Container
// Apps LB health probes), and Analyst-HTTPS at 200 with sourceAddressPrefixes
// = the analyst allowlist (or ["*"] when unlocked). No port 80 rule; the
// Container Apps ingress runs allowInsecure=false and MSAL requires HTTPS
// anyway.

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object

@description('Comma-separated IPv4 CIDRs the container-apps subnet accepts inbound HTTPS from. Empty → no restriction (source *). Applies to every Container App in the environment.')
param allowedIps string = ''

// Convert `1.2.3.4/32,5.6.7.8/32` → the array shape NSG rules expect. Empty
// string → single-element ['*'] so the rule allows all inbound (unlocked).
// Split into two vars because Bicep can't put a for-expression inside a
// ternary (BCP138).
var trimmedAllowedIps = trim(allowedIps)
var splitCidrs = [for cidr in split(trimmedAllowedIps, ','): trim(cidr)]
var cidrList = empty(trimmedAllowedIps) ? [ '*' ] : splitCidrs

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: '${namePrefix}-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      // Container Apps LB health probes MUST be allowed or ingress goes
      // unhealthy the moment we attach the NSG. Not a security concern —
      // Azure Load Balancer is a platform tag Microsoft owns, not
      // reachable from the internet.
      {
        name: 'Allow-AzureLoadBalancer-Inbound'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: 'AzureLoadBalancer'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
          description: 'Container Apps ingress LB health probes.'
        }
      }
      // Analyst HTTPS allowlist. sourceAddressPrefixes = the resolved
      // CIDRs, or ['*'] when unlocked. install.sh reads this back on the
      // next install to preserve the allowlist across runs.
      {
        name: 'Allow-Analysts-Https'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefixes: cidrList
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
          description: 'Analyst allowlist for HTTPS (frontend + backend + MCP). RTD_VIEWER_ALLOWED_IPS / --allowed-ips.'
        }
      }
    ]
  }
}

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
          networkSecurityGroup: { id: nsg.id }
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
output nsgId string = nsg.id
output nsgName string = nsg.name
