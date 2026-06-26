// Storage RBAC for the Container App's managed identity.
//
// Split into its own module because main.bicep is subscription-scoped — it
// can't declare role assignments whose target resource (the storage account)
// lives inside a resource group. The module runs at RG scope, looks up the
// storage account `existing`, and applies the role binding.
//
// Called from main.bicep after both the storage and containerapps modules
// have run, so this module's `dependsOn` is implicit through the params.

targetScope = 'resourceGroup'

@description('Name of the storage account to grant Blob Data Contributor on.')
param storageAccountName string

@description('Managed-identity principal ID of the consuming Container App.')
param principalId string

// Built-in role: Storage Blob Data Contributor. Lets the backend upload
// engagement-export blobs without needing the storage account's connection
// string. Scoped to ONLY this storage account, never higher.
var blobContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

resource role 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storageAccount
  name: guid(storageAccount.id, principalId, blobContributorRoleId)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      blobContributorRoleId
    )
  }
}
