// v2.10.0 Infrastructure tab — subscription-scoped RBAC grants for the
// backend's managed identity. Grants Reader (list VMs, get power state,
// resolve NICs → public IPs) and Virtual Machine Contributor
// (start / deallocate / restart / auto-shutdown schedule PATCH).
//
// Called from main.bicep as part of a `for subId in infraSubsList`
// batch, once per configured subscription. targetScope is subscription
// so `scope: subscription(subId)` in the caller lands here correctly.

targetScope = 'subscription'

@description('Managed-identity principal ID of the backend Container App.')
param principalId string

@description('Built-in Reader role definition GUID.')
param readerRoleId string

@description('Built-in Virtual Machine Contributor role definition GUID.')
param contributorRoleId string

resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().subscriptionId, principalId, readerRoleId)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      readerRoleId
    )
  }
}

resource vmContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().subscriptionId, principalId, contributorRoleId)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      contributorRoleId
    )
  }
}
