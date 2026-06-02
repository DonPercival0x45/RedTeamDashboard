// Postgres Flexible Server — hosts the app schema (engagements, scope,
// findings, approvals, audit_log) plus LangGraph's `checkpoint_*` tables.
//
// Burstable B1ms is the cheapest tier (~$13/mo at on-demand). SSL is forced.
// Phase 0 uses the public endpoint with the "Allow Azure services" firewall
// rule so Container Apps (which has dynamic egress IPs) can connect without
// VNet integration. Phase 1+ should swap to private endpoint inside a VNet.

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object
param postgresVersion string = '16'
param skuName string = 'Standard_B1ms'
param storageSizeGB int = 32
param adminLogin string

@secure()
param adminPassword string

var serverName = '${namePrefix}-pg'
var databaseName = 'rtd'

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: 'Burstable'
  }
  properties: {
    version: postgresVersion
    administratorLogin: adminLogin
    administratorLoginPassword: adminPassword
    storage: {
      storageSizeGB: storageSizeGB
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

// Allow Container Apps + Azure portal queries from any Azure region/sub.
// This is the trade-off for skipping VNet integration in Phase 0.
resource allowAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: server
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource db 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: server
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

output id string = server.id
output name string = server.name
output fqdn string = server.properties.fullyQualifiedDomainName
output databaseName string = databaseName
// Carries the admin password; @secure() keeps it out of deployment-history
// outputs. Consumed only by the Key Vault module's @secure() databaseUrl param.
@secure()
output sqlAlchemyUrl string = 'postgresql+psycopg://${adminLogin}:${adminPassword}@${server.properties.fullyQualifiedDomainName}:5432/${databaseName}?sslmode=require'
