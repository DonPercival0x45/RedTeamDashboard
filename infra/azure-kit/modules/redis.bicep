// Azure Cache for Redis — Streams + pub/sub for runs:* keys.
//
// Basic C0 is the cheapest tier (~$16/mo) and gives us 250 MB. Phase 0
// stream traffic is tiny so this is plenty. Bump to Standard for HA.
//
// SSL-only (port 6380). No firewall rule needed — auth is by primary key.

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object
param skuName string = 'Basic'
param skuFamily string = 'C'
param skuCapacity int = 0  // C0 = 250 MB

var cacheName = '${namePrefix}-redis'

resource cache 'Microsoft.Cache/Redis@2024-11-01' = {
  name: cacheName
  location: location
  tags: tags
  properties: {
    sku: {
      name: skuName
      family: skuFamily
      capacity: skuCapacity
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
    redisVersion: '6'
    publicNetworkAccess: 'Enabled'
  }
}

output id string = cache.id
output name string = cache.name
output hostName string = cache.properties.hostName
@secure()
output primaryKey string = cache.listKeys().primaryKey
// Carries the primary key; @secure() keeps it out of deployment-history
// outputs. Consumed only by the Key Vault module's @secure() redisUrl param.
@secure()
output url string = 'rediss://:${cache.listKeys().primaryKey}@${cache.properties.hostName}:6380/0'
