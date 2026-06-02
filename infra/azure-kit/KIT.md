# Red Team Dashboard — Deployment Kit

Self-hosted Azure deploy. The operator owns every byte of data in their own
tenant; we ship the kit + container images, they run it.

## What gets created

In one resource group (`rtd-<env>`):

| Resource                 | Purpose                                            | ~Monthly |
|--------------------------|----------------------------------------------------|----------|
| Log Analytics workspace  | Container Apps logs                                | $0       |
| Postgres Flexible Server | App data: engagements, findings, approvals, audit  | $15      |
| Azure Cache for Redis    | Run streams + pub/sub                              | $16      |
| Key Vault (RBAC)         | Secrets: DB pw, Redis url, LLM keys, admin key     | $0       |
| Container Apps env + 2   | `backend` (FastAPI) + `worker` (LangGraph)         | $15–30   |

**Floor ~$50/mo**. No ACR (images come from public GHCR). No viewer (use the
central one).

## Prerequisites

Already done if you followed the bootstrap walkthrough:

- Azure CLI installed and `az login` complete
- An Azure subscription selected: `az account set --subscription <name>`
- Resource providers registered in that subscription:
  `Microsoft.App`, `Microsoft.DBforPostgreSQL`, `Microsoft.Cache`,
  `Microsoft.KeyVault`, `Microsoft.OperationalInsights`,
  `Microsoft.ManagedIdentity`
- Bicep CLI: `az bicep install`

## Install

```bash
cd infra/azure-kit
./scripts/install.sh --env prod --location eastus2 --image-tag v0.1.0
```

The installer:

1. Validates `az` is logged in and the subscription is what you expect
2. Generates a Postgres admin password (or accepts one via `--postgres-password`)
3. Runs `az deployment sub create` against `main.bicep` (5–10 min on first run)
4. Captures outputs, waits for the backend to come healthy
5. Prints the **one-time bootstrap commands** you run next

### One-time bootstrap (the installer's last step prints these with your real names)

```bash
# Apply DB migrations
az containerapp exec -n rtd-prod-backend -g rtd-prod \
    --command 'alembic upgrade head'

# Mint the admin API key — SAVE THE OUTPUT, this is the only time it's visible
az containerapp exec -n rtd-prod-backend -g rtd-prod \
    --command 'python -m app.scripts.mint_api_key --name bootstrap --scope admin'

# Stash the key in Key Vault (recoverable from the portal later)
az keyvault secret set --vault-name <KV-NAME> \
    --name admin-api-key --value 'rtd_…'

# Drop in the LLM provider key(s) you'll actually use
az keyvault secret set --vault-name <KV-NAME> --name anthropic-api-key --value 'sk-ant-…'
az keyvault secret set --vault-name <KV-NAME> --name openai-api-key    --value 'sk-…'

# Restart the apps so they pick up the rotated secrets
az containerapp revision restart -n rtd-prod-backend -g rtd-prod \
    --revision $(az containerapp revision list -n rtd-prod-backend -g rtd-prod --query '[0].name' -o tsv)
```

> **Why are these manual?** `az containerapp exec` is interactive (TTY-bound)
> and can't reliably be captured non-interactively today. A follow-up adds a
> Container Apps **Job** that runs these on deploy and writes the key straight
> to Key Vault — eliminating this step. For MVP this two-minute manual step
> is the trade-off.

## After install

You now have:

- A running backend at `https://rtd-prod-backend.<random>.<region>.azurecontainerapps.io`
- An admin API key (saved in Key Vault under `admin-api-key`)
- Empty Postgres + Redis ready for engagements

Next steps:

1. **Install the CLI** (`pip install rtd-cli` — coming in Phase 5):
   ```bash
   rtd login --api-url https://<backend-fqdn> --api-key rtd_<your-admin-key>
   ```
2. **Point the central viewer** at this tenant: add a new connection in the
   viewer's UI with the backend URL + a `viewer`-scoped API key minted via:
   ```bash
   curl -X POST https://<backend-fqdn>/api-keys \
       -H "X-API-Key: rtd_<admin-key>" \
       -H 'Content-Type: application/json' \
       -d '{"name": "central viewer", "scope": "viewer"}'
   ```
3. **Mint a `cli`-scoped key** for the operator account and rotate the admin
   key out of daily use.

## Upgrade (roll a new image tag)

```bash
./scripts/install.sh --env prod --image-tag v0.2.0 --yes
```

Bicep is idempotent on resource names — the only thing that changes is the
container app's image. The roll takes ~30 seconds per app.

## Uninstall

```bash
./scripts/uninstall.sh --env prod --purge
```

Deletes the whole resource group. `--purge` also purges the Key Vault
soft-delete so the name can be reused immediately (otherwise it sits in
soft-deleted state for 7 days).

**Findings, audit logs, and grants survive uninstall only if you exported
them first** — the data was always yours; the kit is just an interpreter.

## Layout

```
infra/azure-kit/
├── KIT.md                        (this file)
├── main.bicep                    subscription-scoped orchestrator
├── main.bicepparam               example parameters
├── modules/
│   ├── containerapps.bicep       backend + worker (GHCR pulls, no ACR)
│   ├── keyvault.bicep            RBAC vault; seeds DB / LLM / admin-key slots
│   ├── loganalytics.bicep        workspace for Container Apps logs
│   ├── postgres.bicep            Flexible Server (B1ms) with Azure-services firewall
│   └── redis.bicep               Cache for Redis (Basic C0)
└── scripts/
    ├── install.sh                deploy driver
    └── uninstall.sh              group + KV purge
```

## What's NOT here yet

- **VNet + private endpoints** — kit ships with public endpoints + the Azure
  services firewall. The `enablePrivateNetworking` param exists as a hook;
  the VNet module that actually flips public endpoints off lands later.
- **Custom domain + TLS** — Container Apps gives `*.azurecontainerapps.io`
  for free. Add `Microsoft.App/managedEnvironments/managedCertificates` +
  a DNS zone when you want a real domain.
- **Container Apps Jobs for bootstrap** — would eliminate the manual
  `alembic upgrade` + `mint_api_key` step. Tracked as a follow-up.
- **Entra OIDC** — auth is API-key today. Drop in an OIDC validator behind
  `app.api.deps.api_key_auth` when ready; the API-key path stays for
  automation (CLI, central viewer connection).
