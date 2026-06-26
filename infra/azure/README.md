# Azure deploy — Project X-Ray

Bicep IaC for the Phase 0 Azure stack. Targets **Container Apps** (consumption tier) — same Dockerfiles as `docker compose`, no Kubernetes to manage.

## Resources provisioned

| Module | Resource | Purpose | Approx. cost (East US) |
|---|---|---|---|
| `loganalytics.bicep` | Log Analytics workspace | Container Apps logs | ~$0 at low ingest |
| `acr.bicep` | Container Registry (Basic) | backend/worker/frontend images | ~$5/mo |
| `postgres.bicep` | Postgres Flexible Server (B1ms) | app schema + LangGraph checkpoints | ~$13/mo |
| `redis.bicep` | Cache for Redis (Basic C0) | inbound/outbound streams | ~$16/mo |
| `keyvault.bicep` | Key Vault (RBAC) | secrets — DB URL, Redis URL, LLM keys | ~$0 |
| `containerapps.bicep` | Container Apps Env + 3 apps | backend, worker, frontend | ~$15-30/mo |

**Floor: ~$50-65/mo** at minimal usage. Costs scale with traffic + image pulls.

## Pre-requisites

```bash
# 1. Azure CLI + Bicep CLI
brew install azure-cli                 # mac
# or: curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
az bicep install

# 2. Login + pick a subscription
az login
az account list --output table
az account set --subscription "<your-subscription-id>"

# 3. Optional: lint the templates locally before deploying
az bicep build --file infra/azure/main.bicep
```

## First deploy

```bash
# Set the postgres admin password as an env var (or inline below).
read -srp "postgres password: " PG_PW; echo

az deployment sub create \
    --name xray-initial \
    --location eastus \
    --template-file infra/azure/main.bicep \
    --parameters infra/azure/main.bicepparam \
    --parameters postgresAdminPassword="$PG_PW"
```

This creates the resource group, all infra, and the three Container Apps. The apps will be **unhealthy after this step** — their image references point at tags that don't exist in ACR yet.

## Build + push images

```bash
# Grab the ACR login server from the deploy outputs
ACR=$(az deployment sub show -n xray-initial --query properties.outputs.acrLoginServer.value -o tsv)
RG=$(az deployment sub show -n xray-initial --query properties.outputs.resourceGroupName.value -o tsv)
TAG=$(git rev-parse --short HEAD)

az acr login -n "${ACR%%.*}"

# Backend image (same image is used for the worker — different command).
docker build -t "$ACR/xray-backend:$TAG" backend/
docker push "$ACR/xray-backend:$TAG"

# Worker uses the same artifact. (Tag separately so we can pin versions
# independently down the line, e.g. canary the worker on a newer image.)
docker tag "$ACR/xray-backend:$TAG" "$ACR/xray-worker:$TAG"
docker push "$ACR/xray-worker:$TAG"

# Frontend — must bake NEXT_PUBLIC_API_BASE at build time.
BACKEND_FQDN=$(az deployment sub show -n xray-initial --query properties.outputs.backendFqdn.value -o tsv)
docker build \
    --build-arg NEXT_PUBLIC_API_BASE="https://$BACKEND_FQDN" \
    --target runner \
    -t "$ACR/xray-frontend:$TAG" frontend/
docker push "$ACR/xray-frontend:$TAG"
```

> The frontend `builder` stage accepts `NEXT_PUBLIC_API_BASE` as a build arg and inlines it into the client bundle. If you omit `--build-arg`, the bundle falls back to `http://localhost:8000` (wrong for a deployed frontend), so always pass the backend FQDN as shown above.

## Roll the new images onto the apps

Either re-run the deploy with the tag…

```bash
az deployment sub create \
    --name xray-roll-$TAG \
    --location eastus \
    --template-file infra/azure/main.bicep \
    --parameters infra/azure/main.bicepparam \
    --parameters postgresAdminPassword="$PG_PW" \
    --parameters backendImageTag=$TAG workerImageTag=$TAG frontendImageTag=$TAG
```

…or update each app directly (faster, no full template eval):

```bash
az containerapp update -n xray-dev-backend  -g $RG --image "$ACR/xray-backend:$TAG"
az containerapp update -n xray-dev-worker   -g $RG --image "$ACR/xray-worker:$TAG"
az containerapp update -n xray-dev-frontend -g $RG --image "$ACR/xray-frontend:$TAG"
```

## Populate the LLM secrets

Key Vault is seeded with `PLACEHOLDER` for every LLM-related key. Rotate them once:

```bash
KV=$(az deployment sub show -n xray-initial --query properties.outputs.keyVaultName.value -o tsv)

# If you're using Azure OpenAI (LLM_PROVIDER=azure):
az keyvault secret set --vault-name $KV --name azure-openai-endpoint   --value "https://<your-aoai-resource>.openai.azure.com"
az keyvault secret set --vault-name $KV --name azure-openai-deployment --value "gpt-4o-mini"
az keyvault secret set --vault-name $KV --name azure-openai-api-key    --value "<key>"

# If you're using Anthropic directly (LLM_PROVIDER=anthropic):
az keyvault secret set --vault-name $KV --name anthropic-api-key --value "sk-ant-..."
```

Restart the apps so they pick up the new secret values:

```bash
az containerapp revision restart -n xray-dev-backend -g $RG --revision $(az containerapp revision list -n xray-dev-backend -g $RG --query "[0].name" -o tsv)
az containerapp revision restart -n xray-dev-worker  -g $RG --revision $(az containerapp revision list -n xray-dev-worker  -g $RG --query "[0].name" -o tsv)
```

## Run the database migrations

Alembic doesn't run automatically. Exec into a backend revision once:

```bash
az containerapp exec -n xray-dev-backend -g $RG --command "alembic upgrade head"
```

## Verify

```bash
BACKEND=$(az deployment sub show -n xray-initial --query properties.outputs.backendFqdn.value -o tsv)
FRONTEND=$(az deployment sub show -n xray-initial --query properties.outputs.frontendFqdn.value -o tsv)

curl "https://$BACKEND/health"
open "https://$FRONTEND"
```

## Teardown

```bash
RG=$(az deployment sub show -n xray-initial --query properties.outputs.resourceGroupName.value -o tsv)
az group delete --name $RG --yes --no-wait

# Key Vault stays in soft-deleted state for 7 days; purge if you want the
# name back immediately:
az keyvault purge --name $(az deployment sub show -n xray-initial --query properties.outputs.keyVaultName.value -o tsv)
```

## What's NOT in here yet

- **Azure OpenAI resource** — quota-gated per region, requires a request form. Create it manually, then drop the endpoint/key/deployment into Key Vault as above.
- **Custom domain + TLS** — Container Apps gives you `*.azurecontainerapps.io` for free. Add `Microsoft.App/managedEnvironments/managedCertificates` + a `Microsoft.Network/dnsZones` record when you want `acme.example.com`.
- **VNet integration / private endpoints** — Phase 0 uses public endpoints with the "Allow Azure services" firewall rule on Postgres. Real prod should add a VNet + private endpoints for Postgres + Redis + Key Vault.
- **Entra OIDC** — auth is X-User-Id header today. Drop in an OIDC validator behind `app.api.deps.current_user` when ready.
- **GitHub Actions CD** — `gh actions secret set` + a `deploy.yml` that runs `az deployment sub create` on push to main. Authoring deferred.

## Files

```
infra/azure/
├── README.md                  (this file)
├── main.bicep                 subscription-scoped orchestrator
├── main.bicepparam            example parameter values
└── modules/
    ├── acr.bicep
    ├── containerapps.bicep
    ├── keyvault.bicep
    ├── loganalytics.bicep
    ├── postgres.bicep
    └── redis.bicep
```
