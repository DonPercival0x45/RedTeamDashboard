// v0.12.0: RG-scoped role assignment so the backend's managed identity
// can create + delete Azure Container Instances for the Tools tab
// sandbox runner (ACIRunner).
//
// Built-in role "Contributor" is scoped to ONLY this resource group
// (not the subscription) so a bug in the runner code can't accidentally
// touch anything outside rtd-<env>. Container Instance Contributor
// exists as a narrower built-in, but it does not include read on the
// storage account for the volume-mount SAS token generation flow; the
// simplest cross-service allowance is Contributor on the RG.

targetScope = 'resourceGroup'

@description('Managed-identity principal ID of the consuming Container App.')
param principalId string

// Built-in: Contributor. Full CRUD on ACI + read on siblings in the RG.
var contributorRoleId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'

resource role 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: resourceGroup()
  name: guid(resourceGroup().id, principalId, contributorRoleId, 'aci-tools')
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      contributorRoleId
    )
  }
}
