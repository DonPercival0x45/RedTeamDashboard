// Self-hosted Redis as a Container App.
//
// Azure Cache for Redis (Microsoft.Cache/Redis) was retired for new
// deployments; Azure Managed Redis starts ~$50-100/mo for the cheapest
// SKU. For a single-user red-team tool whose Redis use is a job queue +
// LangGraph checkpointer + pub/sub of ephemeral run events, a Container
// App running redis:7-alpine costs ~$10/mo and matches the docker-compose
// layout exactly.
//
// Persistence: disabled (`--save '' --appendonly no`). On restart the
// queue + in-flight checkpoints are lost. Acceptable trade-off given
// runs are short-lived and re-submittable from the CLI.
//
// Ingress: internal-only TCP on 6379. Reachable from sibling apps in the
// same env via `<app-name>.internal.<envDefaultDomain>`. No auth — the
// env's internal network is the security boundary.

targetScope = 'resourceGroup'

param namePrefix string
param location string
param tags object
param environmentId string

resource redis 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-redis'
  location: location
  tags: tags
  properties: {
    environmentId: environmentId
    configuration: {
      ingress: {
        external: false
        targetPort: 6379
        exposedPort: 6379
        transport: 'tcp'
      }
    }
    template: {
      containers: [
        {
          name: 'redis'
          image: 'redis:7-alpine'
          command: [ 'redis-server', '--save', '', '--appendonly', 'no' ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

// Internal-only Container App FQDN (e.g. rtd-prod-redis.internal.<domain>).
// `ingress.fqdn` is populated by Azure post-deploy and is the right
// hostname for sibling apps in the same env to connect to.
output hostName string = redis.properties.configuration.ingress.fqdn
output url string = 'redis://${redis.properties.configuration.ingress.fqdn}:6379/0'
output hostPort string = '${redis.properties.configuration.ingress.fqdn}:6379'
