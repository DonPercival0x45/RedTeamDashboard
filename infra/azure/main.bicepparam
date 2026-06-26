// Example parameters for `az deployment sub create` against this template.
//
// Copy to `main.dev.bicepparam` (or per-env) and fill in real values. Do NOT
// commit the populated file with the postgres password in plaintext —
// `az deployment sub create --parameters @main.dev.bicepparam` reads it but
// the file should be gitignored once it has the real secret.

using './main.bicep'

param env = 'dev'
param location = 'eastus'

// Choose a short, lowercase-only prefix that's globally unique enough for ACR
// (alphanumeric only, 5-50 chars). Default is `xray-dev` — change only if you
// hit a name collision in the chosen region.
// param resourceGroupName = 'xray-dev'

param postgresAdminLogin = 'rtdadmin'

// CLI prompt for this is safer than committing. Either:
//   - leave commented out and pass via `--parameters postgresAdminPassword=...`
//   - or set the env var AZURE_POSTGRES_PASSWORD and use `readEnvironmentVariable`
// param postgresAdminPassword = ''

// Image tags. First deploy uses "placeholder" — Container Apps will create
// the app definitions but pulls fail until you push real images. After
// `docker push`, re-run with the real tag.
param backendImageTag = 'placeholder'
param workerImageTag = 'placeholder'
param frontendImageTag = 'placeholder'

// LLM provider for the deployed worker. Choose `azure` if you've provisioned
// an Azure OpenAI resource (Bicep does NOT create it — see README), otherwise
// `anthropic` works too if you populate the anthropic-api-key secret.
param llmProvider = 'azure'
param anthropicModel = 'claude-opus-4-7'
