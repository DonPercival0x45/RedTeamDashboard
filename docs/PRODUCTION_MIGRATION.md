# Project X-Ray — Dev-to-Production Migration Guide

**Purpose:** Step-by-step plan for migrating XR from a local development environment to a
self-hosted Azure production tenant, with a full reference for how the system works, what
it costs, and how to keep it running.

**Audience:** Ken / Nasir — whoever is running the production install.

**Status of code this document assumes:**
- `deployment-security-typing` branch merged: pull-based ACR deployment + ExecutorProtocol
- `phase-11-costs` branch merged: Costs tab, finding importer, JSON export, summary editor, attachments

---

## 1. What This Tool Is

Project X-Ray is a **self-hosted engagement management and governance portal** for
authorized security engagements. It provides:

- A **single pane of glass** for all engagement work: scope definition, findings tracking,
  observations, task management, report generation, and cost attribution.
- **Agent-assisted enumeration** — a Strategic watcher (Claude) observes findings and
  suggests next-step scan/enum tasks; a Tactical dispatcher routes them to the worker.
  Agents run scans and enumeration only. **Analysts perform all validation work.**
- **Approval and validation gates** — every tool invocation requires analyst sign-off
  before running; all raw results sit in `pending_validation` until an analyst reviews them.
- An immutable **audit log** of every action, regardless of entry point (UI, CLI, MCP).
- **Import-first ingest** — Nessus, Maltego, and Dehashed exports upload via the UI;
  tool outputs stay in the analyst's own kit.

### Design principles (from the Charter)

1. **Findings first.** The work product is the center of gravity.
2. **Feedback loop.** Found → finding → tasks → table updates → act → found again.
3. **Analyst in control, agents assist.** Enumeration is automated; validation is human.
4. **Whole-page navigation.** Dark monochrome UI, left nav, ember accent.

---

## 2. Architecture Overview

### Three-tier execution model

```
Analyst (Entra SSO / API key)
        │
        ▼
┌─────────────────────────────────────────┐
│ DASHBOARD (Next.js Static Web App)       │
│ Findings · Entities · Observations       │
│ Scope · Report · Costs · Settings        │
└──────────────┬──────────────────────────┘
               │ HTTPS + Bearer (Entra) or X-API-Key
               ▼
┌─────────────────────────────────────────┐
│ BACKEND CONTROL PLANE (FastAPI)          │
│ Engagements · Findings · Tasks · Scope   │
│ Approvals · Validation · Cost Engine     │
│ MCP server (/mcp/sse) for CLI/agent use  │
└──────┬──────────────────────┬───────────┘
       │ Redis Streams         │ Claude API (LLM)
       ▼                       │
┌────────────────┐     ┌───────▼───────────────┐
│ TACTICAL       │     │ STRATEGIC ("Watcher")   │
│ (Dispatcher)   │◄────│ Observes all findings   │
│ Scanning only  │     │ Suggests tasks + costs   │
│ Approval-gated │     │ Never executes           │
└──────┬─────────┘     └───────────────────────┘
       │ Redis Streams (run.start / run.resume)
       ▼
┌────────────────────────────────┐
│ WORKER (LangGraph, in-pod)      │
│ Passive OSINT + active enum     │
│ Scope gate · Approval gate      │
│ Results land pending_validation │
└────────────────────────────────┘
```

### The three agents

| Agent | Role | Can execute? |
|---|---|---|
| **Strategic** | Observes findings; writes priced suggestions and task lists | Never — pure watcher |
| **Tactical** | Decomposes objectives; dispatches scan/enum tasks | Yes — approval-gated |
| **Worker** | Runs tools (LangGraph); enforces scope | Yes — passive + active enum only |

**Hard invariant:** No agent path can run a `validation`-type task. Validation is
analyst-only. Agents never perform exploitation or validation. Enforced in the tool
registry and at Tactical's dispatch boundary.

### The two gates

1. **Approval gate** — before any active/enumeration tool call, the run interrupts and
   waits for analyst approval. Session grants (`authorizations`) skip re-prompting within
   a session.
2. **Validation gate** — all tool output lands as `pending_validation`. The analyst
   reviews and approves → becomes a validated finding. Rejected results can be re-queued.

### Data storage

| Store | Used for | Azure resource |
|---|---|---|
| Postgres (SQLAlchemy) | All persistent data: engagements, findings, scope, approvals, audit, agent_executions | Postgres Flexible Server |
| Redis (Redis Streams) | Run envelopes between backend ↔ worker; worker checkpoints | Self-hosted Redis container |
| Blob (Azure Storage) | Engagement export archive | Storage Account |
| Key Vault | Secrets: DB password, LLM API keys, worker MCP key, admin API key | Azure Key Vault (RBAC mode) |
| ACR | Container images: xray-backend, xray-worker | Azure Container Registry |

---

## 3. Azure Resource Map

Everything lives in one resource group (`xray-<env>`). Provisioned by `infra/azure-kit/main.bicep`.

| Resource | Name pattern | SKU | ~Monthly cost |
|---|---|---|---|
| Resource Group | `xray-prod` | — | $0 |
| VNet + subnets | `xray-prod-vnet` | Custom /16 | $0 |
| Private DNS Zone | `privatelink.postgres.database.azure.com` | Global | $0 |
| Log Analytics | `xray-prod-logs` | Pay-per-GB | $0–2 |
| Application Insights | `xray-prod-ai` | Workspace-based | $0–2 |
| Postgres Flexible Server | `xray-prod-pg` | B1ms, no public access | ~$15 |
| Key Vault | `xray-prod-kv` | Standard RBAC | $0 |
| Storage Account | `rtdprodsa` | LRS | $0–1 |
| Container Registry | `rtdprodacr` | Standard SKU | ~$5 |
| Container Apps Environment | `xray-prod-cae` | Consumption, VNet-integrated | $0 |
| Container App (main) | `xray-prod-app` | 1 replica: 1.5 vCPU, 3 GiB | ~$20–35 |
| Container App (MCP) | `xray-prod-mcp` | Scale 0–1: 0.5 vCPU, 1 GiB | ~$0–5 idle |
| Static Web App | `xray-prod-swa` | Free tier | $0 |

**Floor: ~$40–45/mo** with light LLM usage. Claude API costs are separate (billed to your
Anthropic account) and tracked in the Costs tab.

### Why Container Registry is now included

The `deployment-security-typing` branch added Azure Container Registry to the kit. Images
previously lived on public GHCR (no auth needed) — the new model pushes to ACR from CI
with `AcrPush` scope only. The Container App updates happen via an ACR Task running inside
Azure with its own Managed Identity (`Container Apps Contributor`). GitHub Actions no longer
has Container Apps access.

---

## 4. Pre-Production Checklist

Complete these before running `install.sh`.

### Azure prerequisites

- [ ] Azure subscription available (not free trial — Postgres Flexible Server is restricted
      on trial subscriptions in some regions)
- [ ] `az login` completed and the correct subscription selected:
      `az account set --subscription "<name>"`
- [ ] Resource providers registered in the subscription:
      ```bash
      az provider register -n Microsoft.App
      az provider register -n Microsoft.DBforPostgreSQL
      az provider register -n Microsoft.Network
      az provider register -n Microsoft.KeyVault
      az provider register -n Microsoft.OperationalInsights
      az provider register -n Microsoft.ManagedIdentity
      az provider register -n Microsoft.ContainerRegistry
      ```
- [ ] Bicep CLI installed: `az bicep install`
- [ ] `openssl` on PATH (used by the installer to generate the Postgres password)
- [ ] Docker on PATH (needed for building + deploying the viewer bundle)
- [ ] Region confirmed: `eastus2` is the default. If you hit
      `LocationIsOfferRestricted` for Postgres, use `centralus` or `westus3`

### GitHub / CI prerequisites

- [ ] Repository forked to your GitHub account (or you have push access to upstream)
- [ ] GitHub Actions enabled on the repo
- [ ] `gh` CLI installed and authenticated: `gh auth login`

### LLM API keys

- [ ] Anthropic API key ready (`sk-ant-…`) — stored in Key Vault post-install
- [ ] OpenAI key if needed (`sk-…`) — optional, stored in Key Vault post-install

### Code prerequisites

- [ ] `deployment-security-typing` merged to `main` (ACR deployment model)
- [ ] `phase-11-costs` merged to `main` (Costs tab, importer, attachments)
- [ ] `main` branch is the branch CI will push images from

---

## 5. Installation Plan

### Step 1 — Run the Bicep installer (5–15 minutes)

```bash
cd infra/azure-kit

# Interactive install (prompts for Postgres password + Anthropic key)
./scripts/install.sh \
  --env prod \
  --location eastus2 \
  --image-tag main

# Or non-interactive (full pre-flight):
./scripts/install.sh \
  --env prod \
  --location eastus2 \
  --image-tag main \
  --anthropic-key "sk-ant-…" \
  --yes
```

**What the installer does:**
1. Validates `az` login and subscription
2. Generates a Postgres admin password (or uses `--postgres-password`)
3. Runs `az deployment sub create` against `main.bicep` — provisions all Azure resources
4. Waits for the backend Container App to pass its health check
5. Forces an initial revision bump to propagate the Managed Identity KV role
6. Builds and deploys the Next.js frontend to the Static Web App
7. Bootstraps the Postgres schema (`alembic upgrade head` runs on Container App startup)
8. Mints the bootstrap admin API key (interactive — you paste it back in)
9. Stores LLM keys in Key Vault
10. Restarts the app to pick up all secrets

**Outputs captured by the installer:**
- `resourceGroupName` — `xray-prod`
- `acrName` — e.g. `rtdprodacr`
- `appFqdn` — the backend URL
- `appName` — the Container App name
- `keyVaultName` — the Key Vault name
- `viewerUrl` — the Static Web App URL

At the end the installer prints the `setup-github-deploy.sh` and `setup-acr-deploy.sh`
commands pre-filled with real values.

---

### Step 2 — Set up CI/CD (GitHub Actions → ACR)

Run these after Step 1 completes, from the repo root:

```bash
# a) Creates the Entra app registration + OIDC federated credential
#    Grants AcrPush on the ACR (not Container Apps Contributor)
#    Writes AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID, AZURE_ACR_NAME
#    as repo variables in GitHub Actions.

AZURE_RG=xray-prod \
AZURE_APP=xray-prod-app \
ACR_NAME=rtdprodacr \
GITHUB_OWNER=<your-github-org-or-user> \
GITHUB_REPO=ProjectXRay \
bash infra/azure-kit/scripts/setup-github-deploy.sh

# b) Creates the ACR Task "deploy-XR" inside Azure.
#    The task fires on every xray-backend:main push and runs az containerapp update
#    using a system-assigned Managed Identity (Container Apps Contributor on the RG).

AZURE_RG=xray-prod \
AZURE_APP=xray-prod-app \
ACR_NAME=rtdprodacr \
bash infra/azure-kit/scripts/setup-acr-deploy.sh
```

**After this, the deployment pipeline is:**
```
git push origin main
    → GitHub Actions (deploy.yml)
        → az acr login (OIDC, AcrPush only)
        → docker build + push :main-<sha> + :main tags
    → ACR webhook triggers ACR Task "deploy-XR"
        → az login --identity (Managed Identity, Container Apps Contributor)
        → az containerapp update (backend + worker)
```

GitHub Actions **cannot** modify the Container App directly. The update is always
mediated by the ACR Task running inside Azure.

---

### Step 3 — Set up Entra SSO (optional but recommended)

Skip if using API-key auth only. Required for per-analyst sign-in via the browser UI.

```bash
AZURE_RG=xray-prod \
AZURE_APP_FQDN=<backend-fqdn> \
VIEWER_URL=<viewer-url> \
bash infra/azure-kit/scripts/setup-entra.sh
```

Then re-run the installer with the Entra IDs to rebuild the frontend:
```bash
./scripts/install.sh \
  --env prod \
  --entra-tenant-id <tenant-id> \
  --entra-client-id <client-id> \
  --yes
```

---

### Step 4 — Mint analyst API keys

Each analyst who will use the CLI or who needs a scoped key:

```bash
# Connect to the backend container
az containerapp exec \
  -n xray-prod-app -g xray-prod \
  --container backend \
  --command 'python -m app.scripts.mint_api_key --name nasir --scope cli'
```

Scopes: `admin` (full access, only for bootstrap), `cli` (analyst operations),
`viewer` (read-only, for the browser viewer connection).

---

### Step 5 — First deployment trigger

Push the current `main` to GitHub to trigger the first full image build and ACR push:

```bash
git checkout main
git push origin main
```

Watch CI: `gh run list --workflow deploy.yml`  
Watch ACR Task: `az acr task list-runs --registry rtdprodacr -o table`

The Container App will roll within ~3 minutes of the push completing.

---

## 6. How It Actually Works — End to End

### A new engagement (from analyst's perspective)

1. **Create engagement** — POST to `/engagements` (via UI or CLI) with target name,
   description, and scope items (IP ranges, domains, URLs).
2. **Scope defined** — scope items are stored in `scope_items`; every tool call is
   checked against this gate automatically (out-of-scope = auto-rejected, never queued).
3. **Start a run** — the analyst triggers a scan/enum run. The backend publishes a
   `run.start` envelope to the engagement's Redis Stream.
4. **Worker picks up the run** — `StreamConsumer` polls active engagement streams;
   `RunRunner` hydrates the envelope and fires a LangGraph execution.
5. **Graph runs** — the LangGraph agent calls tools. Each tool call hits the scope gate
   and (for active tools) an approval gate. The graph pauses at approvals, waits for
   the analyst to respond, then resumes.
6. **Results land** — every finding the agent creates is written as
   `status=pending_validation`. The agent emits lifecycle events to a Redis output
   stream that the frontend subscribes to via SSE.
7. **Analyst validates** — the Findings tab shows pending items; the analyst clicks
   each, reviews, and approves (→ `validated`) or rejects. Only validated findings
   appear in the PDF report.
8. **Strategic reacts** — on each `finding.created` event, the Strategic agent runs,
   reads the full engagement context, and writes `Suggestion` rows for the analyst.
9. **Loop continues** — the analyst can accept a suggestion (which creates a Task and
   dispatches it via Tactical), add their own task, or do the work manually and upload.

### Import path (analyst-run tools)

For heavy tools (Nessus, Maltego, Dehashed), the analyst runs them on their own
infrastructure and uploads the output file to the UI. The importers parse the upload
and create findings or entities directly, skipping the agent execution path entirely.

---

## 7. Component Reference

### Backend (`backend/`)
- **FastAPI** app at `app/main.py` — REST API + SSE event streaming
- **LangGraph worker** at `app/worker/` — consumes Redis Streams, runs graph execution
- **MCP server** at `app/mcp/` — exposes tools over SSE for CLI/external agent use
- **Orchestrator** at `app/orchestrator/` — LangGraph graph definition, tools, scope/gate logic
- **Agents** at `app/agents/` — Strategic watcher, Tactical dispatcher
- **Models** at `app/models/` — SQLAlchemy ORM (Engagement, Finding, Task, Approval, etc.)
- **Alembic** at `alembic/` — database migrations, single linear chain (currently head `0009`)
- **Importers** at `app/services/` — nessus_import, maltego_import, darkweb_import, scope_import

### Frontend (`frontend/`)
- **Next.js** static export deployed to Azure Static Web App
- Left nav: Findings · Entities · Observations · Report · Costs · Scope
- Entra MSAL.js authentication (if SSO configured) or API key auth
- SSE subscription to backend for live finding/event updates

### Infrastructure (`infra/azure-kit/`)
- `main.bicep` — subscription-scoped orchestrator
- `modules/acr.bicep` — Container Registry (Standard SKU)
- `modules/containerapps.bicep` — main app (backend + worker + redis containers, co-located)
- `modules/mcp_app.bicep` — secondary MCP app (scale-to-zero, isolated)
- `modules/containerappsenv.bicep` — Container Apps Environment (VNet-integrated)
- `modules/keyvault.bicep` — RBAC mode KV with seeded secret slots
- `modules/postgres.bicep` — Flexible Server B1ms, VNet-injected, no public access
- `modules/storage.bicep` — Blob storage for engagement export archives
- `modules/vnet.bicep`, `modules/loganalytics.bicep`, `modules/appinsights.bicep`, `modules/viewer.bicep`
- `acr-deploy-task.yaml` — ACR Task definition that runs `az containerapp update`
- `scripts/install.sh` — full install driver
- `scripts/setup-github-deploy.sh` — OIDC + AcrPush wiring
- `scripts/setup-acr-deploy.sh` — ACR Task + Managed Identity setup
- `scripts/setup-entra.sh` — Entra app registration for analyst SSO
- `scripts/uninstall.sh` — full teardown + KV purge

### CI/CD (`.github/workflows/`)
- `ci.yml` — runs tests on every PR
- `deploy.yml` — manual dispatch: builds + pushes to ACR; ACR Task rolls the app
- `release.yml` — tags + changelog

---

## 8. Complexity Map — Moving Parts

This is the full dependency graph. Each item is a failure point to understand before
going to production.

```
GitHub Actions (OIDC → AcrPush)
  └─► Azure Container Registry (Standard)
        ├─► ACR Task "deploy-XR" (system MI → Container Apps Contributor)
        │     └─► Container App xray-prod-app
        │           ├─► backend container (1 vCPU, 2 GiB)
        │           │     ├─► Postgres (VNet-injected, B1ms)
        │           │     ├─► Redis (127.0.0.1:6379, co-located)
        │           │     ├─► Key Vault (Managed Identity → KV Secrets User)
        │           │     ├─► Storage Account (Managed Identity → Blob Contributor)
        │           │     └─► Anthropic/OpenAI API (outbound HTTPS)
        │           └─► worker container (0.25 vCPU, 0.5 GiB)
        │                 └─► same KV/Redis/Postgres as backend
        └─► Container App xray-prod-mcp (scale-to-zero)
              └─► MCP server (SSE, auth-gated by lease token)

Static Web App (Free tier)
  └─► calls backend FQDN for all API traffic

LangGraph execution (inside worker)
  └─► Anthropic Claude API (ANTHROPIC_API_KEY from KV)
  └─► Scope gate (in-process, queries Postgres)
  └─► Approval gate (interrupts, waits for Redis resume message from backend)
  └─► Tool calls: DNS, WHOIS, port scan, HTTP probe, subfinder, etc.
```

### Complexity hotspots

| Component | Why it's complex | Mitigation |
|---|---|---|
| ACR Task | Runs in Azure; system MI must propagate before first trigger fires | Wait 5 min after `setup-acr-deploy.sh` before first push |
| Container App co-location | backend + worker + redis share one pod; no independent scaling | By design — single operator tool; documented trade-off |
| Redis Streams | Run envelopes + approval resumes pass through Redis; Redis restart loses in-flight runs | Runs are re-startable; approvals timeout gracefully |
| Postgres VNet injection | DB not publicly accessible; only reachable from within the VNet | `az containerapp exec` for manual DB access |
| Alembic migrations | Linear chain; run automatically on backend container startup | Never branch the chain; always test migrations locally first |
| Entra SSO | Separate Entra app registration; redirect URIs must match SWA URL | setup-entra.sh handles this; re-run if SWA URL changes |
| KV Managed Identity | Role propagation takes ~5 min after deploy; app fails on startup until it propagates | Installer forces a revision bump and waits for health |
| LLM keys in KV | Worker and backend read from KV on startup; missing key = startup failure | Installer stores keys before restart; monitor Container App logs |
| Scope gate enforcement | Runs as a pure function in the worker; not a network firewall | Gate logic is in `app/orchestrator/gate.py` — code review critical |

---

## 9. Ongoing Maintenance

### Deploying a new version

Push to `main` on the configured GitHub repo. The pipeline is automatic:
1. CI tests pass on PR merge
2. `deploy.yml` builds and pushes to ACR
3. ACR Task `deploy-XR` rolls the Container App (~2 min)

To monitor: `az acr task list-runs --registry rtdprodacr -o table`

To force a manual deploy without a code change:
```bash
az acr task run --registry rtdprodacr --name deploy-XR
```

### Database migrations

Migrations run automatically on every backend container startup (`alembic upgrade head`
is the first command in the backend's `command` spec). This is idempotent — if the DB
is already at the latest head it's a no-op.

To check current migration state:
```bash
az containerapp exec -n xray-prod-app -g xray-prod \
  --container backend --command 'alembic current'
```

If a migration fails, the backend will not start. Check logs:
```bash
az containerapp logs show -n xray-prod-app -g xray-prod --container backend --follow
```

### Secret rotation

**LLM API keys** (most common rotation):
```bash
# Update the KV secret
az keyvault secret set --vault-name xray-prod-kv \
  --name anthropic-api-key --value 'sk-ant-…new…'

# Restart to pick up the new secret
az containerapp revision restart \
  -n xray-prod-app -g xray-prod \
  --revision $(az containerapp revision list -n xray-prod-app -g xray-prod \
               --query '[?properties.active].name | [0]' -o tsv)
```

**Worker MCP API key** (rotated when a new cli-scoped key is minted):
```bash
az keyvault secret set --vault-name xray-prod-kv \
  --name worker-mcp-api-key --value 'xr_…new…'
# Restart app as above
```

**Postgres password** (annual rotation recommended):
1. Update Postgres server password: `az postgres flexible-server update …`
2. Update the `database-url` secret in KV
3. Restart the Container App

### Monitoring and alerting

Log Analytics workspace collects all Container App logs. Query via:
```bash
# Last 100 error lines from the backend
az monitor log-analytics query \
  --workspace $(az monitor log-analytics workspace show \
    -n xray-prod-logs -g xray-prod --query customerId -o tsv) \
  --analytics-query \
    "ContainerAppConsoleLogs_CL | where ContainerName_s == 'backend' \
     | where Log_s contains 'ERROR' | order by TimeGenerated desc | take 100"
```

Application Insights (`xray-prod-ai`) captures structured telemetry when
`APPLICATIONINSIGHTS_CONNECTION_STRING` is set (it is, by default).

Set up an alert rule in the Azure portal on `ContainerAppConsoleLogs_CL` for
`level=error` or on the `/health` endpoint availability if you want pager-style
alerting.

### Backups

Postgres Flexible Server has **automated backups enabled** (7-day retention by default
on B1ms). Point-in-time restore available via Azure portal or:
```bash
az postgres flexible-server restore \
  --source-server xray-prod-pg \
  --restore-time "2026-06-20T12:00:00Z" \
  --name xray-prod-pg-restore \
  --resource-group xray-prod
```

ACR images: ACR retains images for 7 days (configured in `acr.bicep`). The immutable
SHA-tagged images (`:main-<sha>`) remain in the registry for rollback.

**No built-in blob storage backup** is configured. If engagement export archives
matter, add a lifecycle policy or a periodic Azure Backup job on the storage account.

### Rollback

If a deploy breaks the app:
```bash
# List recent revisions
az containerapp revision list -n xray-prod-app -g xray-prod -o table

# Activate the previous good revision
az containerapp revision activate \
  -n xray-prod-app -g xray-prod \
  --revision xray-prod-app--<previous-revision-name>

# Force 100% traffic to it
az containerapp ingress traffic set \
  -n xray-prod-app -g xray-prod \
  --revision-weight xray-prod-app--<previous-revision-name>=100
```

The previous image is still in ACR under its `:main-<sha>` tag.

---

## 10. Dev vs. Production Differences

| Aspect | Dev | Production |
|---|---|---|
| Database | Docker Compose Postgres on localhost:5432 | Azure Postgres Flexible Server (VNet-injected, no public endpoint) |
| Redis | Docker Compose Redis on localhost:6379 | Self-hosted Redis container, co-located at 127.0.0.1:6379 |
| Images | Built locally via `docker compose up` | Built in GitHub Actions, pushed to ACR, rolled via ACR Task |
| Auth | API key (`xr_MASTER_KEY` in env) | API keys stored in Key Vault; Entra SSO for browser UI |
| Secrets | `.env` file or shell exports | Azure Key Vault (secrets referenced via Managed Identity) |
| Deployments | `docker compose up --build` | Git push → CI → ACR Task auto-roll |
| Migrations | `alembic upgrade head` manually | Runs automatically on container startup |
| Frontend | `npm run dev` on localhost:3001 | Next.js static export deployed to Azure Static Web App |
| LLM keys | Shell environment variable | Key Vault secret `anthropic-api-key` |
| Scale | Single process | Single replica (by design — localhost Redis sharing) |
| Monitoring | Stdout / pytest | Log Analytics + Application Insights |

### What does NOT change dev → prod

- The code itself — no environment-specific branches or flags
- The Postgres schema — same Alembic migrations, same models
- The API surface — same endpoints, same auth model
- The agent logic — same LangGraph graph, same tools, same gates
- The import parsers — Nessus/Maltego/Dehashed work identically

### What IS different that can catch you

1. **VNet-injected Postgres** — you cannot connect to it with `psql` from your laptop.
   Use `az containerapp exec` into the backend container, or set up a Bastion/jump host.
2. **Cold-start latency** — Container Apps on Consumption can have ~5–10s cold starts
   if the app has been idle. The `/health` probe handles this, but first requests after
   long idle periods will be slow.
3. **KV role propagation** — after deploy, the Managed Identity → KV Secrets User role
   takes ~5 minutes to propagate. The installer handles this with a sleep + revision
   bump, but manual re-deploys immediately after a Bicep change may hit this window.
4. **ACR Task lag** — the ACR Task fires on image push, not on workflow completion.
   There is typically a 30–90s gap between the GitHub Actions push step completing and
   the Container App finishing its roll. Plan for this in deployment coordination.
5. **Session grants** — approval `authorizations` (session grants that skip per-call
   approval prompts) are stored in Postgres. They persist across deploys. Revoke them
   via the API if you want a clean slate after a major version bump.

---

## 11. Teardown

```bash
# Delete everything — Postgres data, findings, audit logs, ALL.
# Only do this if the engagement data has been exported first.
bash infra/azure-kit/scripts/uninstall.sh --env prod --purge
```

`--purge` deletes the Key Vault immediately (bypasses 7-day soft-delete). Omit it if
you want the option to recover secrets from the soft-deleted vault.

The ACR images are deleted with the resource group. Download any images you want to
keep first:
```bash
docker pull rtdprodacr.azurecr.io/xray-backend:main-<sha>
docker save rtdprodacr.azurecr.io/xray-backend:main-<sha> | gzip > xray-backend-<sha>.tar.gz
```

---

## 12. Quick-Reference Command Card

```bash
# === INSTALL ===
cd infra/azure-kit
./scripts/install.sh --env prod --location eastus2 --image-tag main

# === CI/CD WIRING ===
AZURE_RG=xray-prod AZURE_APP=xray-prod-app ACR_NAME=rtdprodacr \
GITHUB_OWNER=<you> GITHUB_REPO=ProjectXRay \
bash scripts/setup-github-deploy.sh

AZURE_RG=xray-prod AZURE_APP=xray-prod-app ACR_NAME=rtdprodacr \
bash scripts/setup-acr-deploy.sh

# === DEPLOY (manual trigger) ===
gh workflow run deploy.yml
# or: git push origin main

# === MONITOR DEPLOY ===
az acr task list-runs --registry rtdprodacr -o table

# === ROLLBACK ===
az containerapp revision list -n xray-prod-app -g xray-prod -o table
az containerapp revision activate -n xray-prod-app -g xray-prod --revision <name>

# === ACCESS LOGS ===
az containerapp logs show -n xray-prod-app -g xray-prod --container backend --follow

# === SHELL INTO BACKEND ===
az containerapp exec -n xray-prod-app -g xray-prod --container backend

# === MINT ANALYST KEY ===
az containerapp exec -n xray-prod-app -g xray-prod --container backend \
  --command 'python -m app.scripts.mint_api_key --name <name> --scope cli'

# === ROTATE LLM KEY ===
az keyvault secret set --vault-name xray-prod-kv --name anthropic-api-key --value 'sk-ant-…'
az containerapp revision restart -n xray-prod-app -g xray-prod \
  --revision $(az containerapp revision list -n xray-prod-app -g xray-prod \
               --query '[?properties.active].name | [0]' -o tsv)

# === TEARDOWN ===
bash scripts/uninstall.sh --env prod --purge
```

---

*Last updated: 2026-06-26*  
*Maintainers: Ken + Nasir*
