#!/usr/bin/env bash
# Red Team Dashboard — Deployment Kit installer.
#
# One-shot install: provisions every Azure resource the kit needs in the
# subscription you've already selected with `az account set`. Re-runnable —
# Bicep deploys are idempotent on resource names. Re-running with a new
# --image-tag rolls the apps without recreating the data plane.
#
# Usage:
#     ./install.sh                              # interactive
#     ./install.sh --env prod --location centralus --image-tag v0.1.0
#
# Prereqs (also enforced below):
#   - az logged in: `az login`
#   - az subscription selected: `az account set --subscription <name>`
#   - Bicep CLI installed: `az bicep install`
#   - openssl on PATH (for generating the postgres password)
#   - docker on PATH (for building + deploying the viewer bundle)
#
# LLM API keys:
#   Pass --anthropic-key or set ANTHROPIC_API_KEY in the environment.
#   Pass --openai-key or set OPENAI_API_KEY in the environment.
#   Keys are written directly to Key Vault — never stored in the script.

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults + arg parsing
# ---------------------------------------------------------------------------

ENV_NAME="prod"
LOCATION="eastus2"
IMAGE_REPO_OWNER="donpercival0x45"
IMAGE_TAG="latest"
LLM_PROVIDER="anthropic"
PG_PW=""
ENTRA_TENANT_ID="${RTD_ENTRA_TENANT_ID:-}"
ENTRA_CLIENT_ID="${RTD_ENTRA_CLIENT_ID:-}"
ENTRA_TENANT_ID_FROM_FLAG=false
ENTRA_CLIENT_ID_FROM_FLAG=false
ANTHROPIC_KEY="${ANTHROPIC_API_KEY:-}"
OPENAI_KEY="${OPENAI_API_KEY:-}"
PLANNER_KEY="${PLANNER_API_KEY:-}"
# Comma-separated list of IPv4 CIDRs allowed inbound HTTPS to the whole
# Container Apps environment (frontend + backend + MCP). Empty → no
# restriction (wide open).
# Resolution precedence (highest first), all evaluated pre-Bicep:
#   1. --allowed-ips flag (explicit; empty value clears the lock)
#   2. Live ipSecurityRestrictions on the frontend Container App ingress
#      (ipAddressRange values joined with commas)
#   3. RTD_VIEWER_ALLOWED_IPS shell env
# The Bicep deploy stamps the resolved value straight into ingress
# `ipSecurityRestrictions` on frontend + backend + MCP. The next install
# reads it back from the live frontend ingress. Set once, install many
# times.
#
# v1.28.1: enforcement REVERTED to per-app ingress ipSecurityRestrictions
# on frontend + backend + MCP. v1.28.0 tried moving it to a subnet NSG,
# but Container Apps external envs put a shared load balancer in front
# that SNATs the client IP — the subnet NSG only ever sees
# `AzureLoadBalancer` as source and the analyst-CIDR rule never
# matches, effectively allowing everything through. Only Envoy at the
# ingress layer preserves the real client IP (X-Forwarded-For), which
# is what ipSecurityRestrictions gates on. Scope stays env-wide (CLI +
# MCP + browser all filtered by the same list) — the v1.28.0 behavior
# change from v1.27 is preserved.
#
# v1.10.0: SWA (rtd-<env>-viewer) has been decommissioned. Prior source
# of truth was the SWA's Environment Variables blade; that path is gone.
#
# The same precedence applies to RTD_ENTRA_TENANT_ID and RTD_ENTRA_CLIENT_ID:
# pass --entra-tenant-id / --entra-client-id once, and every subsequent
# install resolves them from the frontend Container App's env vars so the
# analyst's SSO doesn't silently regress to "analyst@localhost" if a teammate
# runs install.sh without the flags (see the v0.7.0 fix for the 2026-06-30
# 5qprod gotcha; source moved from SWA blade → Container App env in v1.10.0).
ALLOWED_IPS="${RTD_VIEWER_ALLOWED_IPS:-}"
ALLOWED_IPS_FROM_FLAG=false
# v2.10.0 Infrastructure tab — CSV of Azure subscription IDs the admin
# Infrastructure tab surfaces + controls. Same resolve precedence as
# --allowed-ips: flag → live backend Container App env → shell env var.
INFRA_SUBSCRIPTIONS="${RTD_INFRA_SUBSCRIPTIONS:-${INFRA_SUBSCRIPTIONS:-}}"
INFRA_SUBSCRIPTIONS_FROM_FLAG=false
NON_INTERACTIVE=false

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  --env NAME              Short env name; used in every resource name (default: prod)
  --location REGION       Azure region (default: eastus2)
  --image-repo-owner OWNER GHCR owner where rtd-{backend,worker} are published (default: donpercival0x45)
  --image-tag TAG         Image tag to deploy (default: latest)
  --llm-provider P        anthropic | openai | azure (default: anthropic)
  --postgres-password PW  Provide the postgres password; otherwise one is generated.
  --anthropic-key KEY     Anthropic API key to store in Key Vault. Falls back to
                          ANTHROPIC_API_KEY env var. Prompted if neither is set and
                          llm-provider is anthropic.
  --openai-key KEY        OpenAI API key to store in Key Vault. Falls back to
                          OPENAI_API_KEY env var.
  PLANNER_API_KEY env     Dedicated Suggestion Box evaluation key. Optional;
                          falls back to the org provider key when unset.
  --entra-tenant-id ID    Entra tenant id for analyst SSO (from setup-entra.sh).
                          Optional. Persists as an env var (RTD_ENTRA_TENANT_ID)
                          on the frontend Container App via Bicep for subsequent
                          installs. Falls back to: live frontend Container App
                          env vars, then shell RTD_ENTRA_TENANT_ID.
  --entra-client-id ID    Entra app (client) id for analyst SSO.
                          Optional. Persists as an env var (RTD_ENTRA_CLIENT_ID)
                          on the frontend Container App via Bicep for subsequent
                          installs. Falls back to: live frontend Container App
                          env vars, then shell RTD_ENTRA_CLIENT_ID.
  --allowed-ips CSV       Comma-separated IPv4 CIDRs allowed inbound HTTPS to
                          the whole Container Apps environment — frontend AND
                          backend AND MCP (e.g. '1.2.3.4/32,5.6.7.8/32').
                          Empty → no IP restriction. v1.28.1: persists to
                          per-app ingress `ipSecurityRestrictions` on all
                          three Container Apps via Bicep for the next
                          install. Falls back to: live frontend Container App
                          ingress rules (if already set), then shell
                          RTD_VIEWER_ALLOWED_IPS. Note: CLI + MCP clients
                          need to be in the allowlist too.
  --infra-subscriptions CSV  v2.10.0 Infrastructure tab. Comma-separated
                          Azure subscription IDs whose VMs the admin
                          Infrastructure tab should surface + control.
                          Grants the backend's managed identity Reader +
                          Virtual Machine Contributor at EACH sub's scope.
                          WARNING: a compromised backend can start / stop /
                          deallocate every VM in every listed sub. Falls
                          back to: live backend Container App env
                          (INFRA_SUBSCRIPTIONS), then shell env
                          (RTD_INFRA_SUBSCRIPTIONS or INFRA_SUBSCRIPTIONS).
  --yes                   Skip the confirmation prompt; useful in CI/automation.
  -h, --help              Show this help.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)               ENV_NAME="$2";         shift 2 ;;
        --location)          LOCATION="$2";          shift 2 ;;
        --image-repo-owner)  IMAGE_REPO_OWNER="$2";  shift 2 ;;
        --image-tag)         IMAGE_TAG="$2";          shift 2 ;;
        --llm-provider)      LLM_PROVIDER="$2";       shift 2 ;;
        --postgres-password) PG_PW="$2";              shift 2 ;;
        --anthropic-key)     ANTHROPIC_KEY="$2";      shift 2 ;;
        --openai-key)        OPENAI_KEY="$2";          shift 2 ;;
        --entra-tenant-id)   ENTRA_TENANT_ID="$2"; ENTRA_TENANT_ID_FROM_FLAG=true; shift 2 ;;
        --entra-client-id)   ENTRA_CLIENT_ID="$2"; ENTRA_CLIENT_ID_FROM_FLAG=true; shift 2 ;;
        --allowed-ips)       ALLOWED_IPS="$2"; ALLOWED_IPS_FROM_FLAG=true; shift 2 ;;
        --infra-subscriptions) INFRA_SUBSCRIPTIONS="$2"; INFRA_SUBSCRIPTIONS_FROM_FLAG=true; shift 2 ;;
        --yes)               NON_INTERACTIVE=true;     shift ;;
        -h|--help)           usage ;;
        *) echo "unknown arg: $1" >&2; usage ;;
    esac
done

RG_NAME="rtd-${ENV_NAME}"
DEPLOY_NAME="rtd-${ENV_NAME}-$(date +%Y%m%d%H%M%S)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_ROOT="$(dirname "$HERE")"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

red()   { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
blue()  { printf "\033[34m%s\033[0m\n" "$*"; }
bold()  { printf "\033[1m%s\033[0m\n" "$*"; }

die() { red "error: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"; }

# Run a command inside the backend container. `az containerapp exec` requires
# a TTY; `script` provides one. Syntax differs between Linux (util-linux) and
# macOS (BSD script).
container_exec() {
    local cmd="$1"
    if [[ "$(uname)" == "Darwin" ]]; then
        script -q /dev/null az containerapp exec \
            -n "$APP_NAME" -g "$RG_OUT" --container backend --command "$cmd"
    else
        script -qc "az containerapp exec \
            -n '$APP_NAME' -g '$RG_OUT' --container backend --command '$cmd'" /dev/null
    fi
}

# ---------------------------------------------------------------------------
# Prereq checks
# ---------------------------------------------------------------------------

bold "[1/6] Checking prerequisites…"
need az
need openssl
az bicep version >/dev/null 2>&1 || die "Bicep CLI missing — run 'az bicep install'"

SUB_INFO="$(az account show -o json 2>/dev/null || true)"
[[ -z "$SUB_INFO" ]] && die "not logged in. Run 'az login' first."
SUB_NAME="$(echo "$SUB_INFO" | python3 -c 'import sys,json;print(json.load(sys.stdin)["name"])')"
TENANT_ID="$(echo "$SUB_INFO" | python3 -c 'import sys,json;print(json.load(sys.stdin)["tenantId"])')"

echo "    Subscription: $SUB_NAME"
echo "    Tenant:       $TENANT_ID"
echo "    Region:       $LOCATION"
echo "    Resource group: $RG_NAME"
echo "    Image:        ghcr.io/$IMAGE_REPO_OWNER/rtd-{backend,worker}:$IMAGE_TAG"
echo "    LLM provider: $LLM_PROVIDER"
echo

# Prompt for LLM key if needed and not already provided
if [[ "$LLM_PROVIDER" == "anthropic" && -z "$ANTHROPIC_KEY" && "$NON_INTERACTIVE" != "true" ]]; then
    read -rsp "    Anthropic API key (sk-ant-…): " ANTHROPIC_KEY
    echo
    [[ -z "$ANTHROPIC_KEY" ]] && die "Anthropic API key is required when llm-provider=anthropic"
fi

if [[ "$NON_INTERACTIVE" != "true" ]]; then
    read -rp "Proceed with this configuration? [y/N] " ack
    [[ "$ack" =~ ^[Yy]$ ]] || { echo "aborted."; exit 1; }
fi

# ---------------------------------------------------------------------------
# Postgres password
# ---------------------------------------------------------------------------

if [[ -z "$PG_PW" ]]; then
    PG_PW="$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)Aa1!"
    bold "[2/6] Generated postgres password (stored in Key Vault as 'postgres-password')."
else
    bold "[2/6] Using provided postgres password."
fi

# ---------------------------------------------------------------------------
# Pre-Bicep: resolve Entra IDs and IP allowlist from the LIVE frontend
# Container App if no CLI flag was passed. Both are stamped by Bicep on
# every deploy, so live state IS the source of truth for what a prior
# install established.
#
# The Bicep deploy below then stamps whatever we resolved back onto the
# frontend Container App (Entra IDs gate ``settings.entra_enabled``
# server-side — empty values silently drop every Bearer token to a
# "header required" 401, the v0.7.1 prod bug) and onto per-app ingress
# ipSecurityRestrictions on frontend + backend + MCP.
#
# On a first install neither resource exists yet, the queries 404, and we
# fall through to whatever the CLI flags / shell env provided. No-op.
#
# v1.10.0: Entra source moved from SWA app settings → frontend Container
# App env after SWA decommission.
# v1.28.1: IP allowlist source reverted from NSG rule → frontend
# ingress ipSecurityRestrictions (see the ALLOWED_IPS comment block
# above for the full v1.28.0 postmortem). Coverage stays env-wide via
# ipSecurityRestrictions on frontend + backend + MCP.
FRONTEND_APP_PREDICTED_NAME="rtd-${ENV_NAME}-frontend"
if [[ "$ENTRA_TENANT_ID_FROM_FLAG" != "true" ]]; then
    PRE_STORED_TENANT="$(az containerapp show \
        -n "$FRONTEND_APP_PREDICTED_NAME" -g "$RG_NAME" \
        --query "properties.template.containers[0].env[?name=='RTD_ENTRA_TENANT_ID'].value | [0]" \
        -o tsv 2>/dev/null || true)"
    if [[ -n "$PRE_STORED_TENANT" && "$PRE_STORED_TENANT" != "None" ]]; then
        ENTRA_TENANT_ID="$PRE_STORED_TENANT"
        blue "    Entra tenant id pre-resolved from frontend Container App env"
    fi
fi
if [[ "$ENTRA_CLIENT_ID_FROM_FLAG" != "true" ]]; then
    PRE_STORED_CLIENT="$(az containerapp show \
        -n "$FRONTEND_APP_PREDICTED_NAME" -g "$RG_NAME" \
        --query "properties.template.containers[0].env[?name=='RTD_ENTRA_CLIENT_ID'].value | [0]" \
        -o tsv 2>/dev/null || true)"
    if [[ -n "$PRE_STORED_CLIENT" && "$PRE_STORED_CLIENT" != "None" ]]; then
        ENTRA_CLIENT_ID="$PRE_STORED_CLIENT"
        blue "    Entra client id pre-resolved from frontend Container App env"
    fi
fi

# v2.10.0 Infrastructure subscriptions — same live-env resolution pattern.
# Source of truth is the INFRA_SUBSCRIPTIONS env var on the BACKEND
# Container App (v2.10.3: renamed from RTD_INFRA_SUBSCRIPTIONS so pydantic-
# settings' field-name → uppercase env var mapping picks it up; every
# other setting in app/core/config.py follows the same no-prefix rule).
# Falls back to the pre-v2.10.3 name for continuity on already-installed envs.
BACKEND_APP_PREDICTED_NAME="rtd-${ENV_NAME}-app"
if [[ "$INFRA_SUBSCRIPTIONS_FROM_FLAG" != "true" ]]; then
    PRE_STORED_INFRA_SUBS="$(az containerapp show \
        -n "$BACKEND_APP_PREDICTED_NAME" -g "$RG_NAME" \
        --query "properties.template.containers[?name=='backend'].env[?name=='INFRA_SUBSCRIPTIONS' || name=='RTD_INFRA_SUBSCRIPTIONS'].value | [0]" \
        -o tsv 2>/dev/null || true)"
    if [[ -n "$PRE_STORED_INFRA_SUBS" && "$PRE_STORED_INFRA_SUBS" != "None" ]]; then
        INFRA_SUBSCRIPTIONS="$PRE_STORED_INFRA_SUBS"
        blue "    Infra subscriptions pre-resolved from backend Container App env"
    fi
fi

# IP allowlist source of truth is the LIVE ingress ipSecurityRestrictions
# on the frontend Container App — read the ipAddressRange values back and
# re-flatten to CSV so the Bicep param below re-stamps them onto all
# three apps (frontend + backend + MCP). Only Allow rules are used; a
# fully-locked env has [{action: Allow, ...}, {action: Allow, ...}] for
# each analyst CIDR. An unlocked env has no rules, so the query returns
# empty and we leave ALLOWED_IPS as whatever the CLI/env provided.
if [[ "$ALLOWED_IPS_FROM_FLAG" != "true" ]]; then
    PRE_STORED_IPS="$(az containerapp show \
        -n "$FRONTEND_APP_PREDICTED_NAME" -g "$RG_NAME" \
        --query "properties.configuration.ingress.ipSecurityRestrictions[?action=='Allow'].ipAddressRange | join(',', @)" \
        -o tsv 2>/dev/null || true)"
    if [[ -n "$PRE_STORED_IPS" && "$PRE_STORED_IPS" != "None" ]]; then
        ALLOWED_IPS="$PRE_STORED_IPS"
        blue "    IP allowlist pre-resolved from frontend ingress ipSecurityRestrictions"
    fi
fi

# ---------------------------------------------------------------------------
# Bicep deploy
# ---------------------------------------------------------------------------

bold "[3/6] Running Bicep deploy '$DEPLOY_NAME'… (5-10 minutes for first run)"

GHCR_IMAGE_TAG="${IMAGE_TAG#v}"

az deployment sub create \
    --name "$DEPLOY_NAME" \
    --location "$LOCATION" \
    --template-file "$KIT_ROOT/main.bicep" \
    --parameters env="$ENV_NAME" \
    --parameters location="$LOCATION" \
    --parameters postgresAdminPassword="$PG_PW" \
    --parameters imageRepoOwner="$IMAGE_REPO_OWNER" \
    --parameters imageTag="$GHCR_IMAGE_TAG" \
    --parameters llmProvider="$LLM_PROVIDER" \
    --parameters entraTenantId="$ENTRA_TENANT_ID" \
    --parameters entraClientId="$ENTRA_CLIENT_ID" \
    --parameters allowedIps="$ALLOWED_IPS" \
    --parameters infraSubscriptions="$INFRA_SUBSCRIPTIONS" \
    --only-show-errors \
    -o none

# ---------------------------------------------------------------------------
# Pull outputs
# ---------------------------------------------------------------------------

bold "[4/6] Capturing deployment outputs…"

OUTPUTS="$(az deployment sub show -n "$DEPLOY_NAME" --query properties.outputs -o json)"

RG_OUT="$(echo "$OUTPUTS"     | python3 -c 'import sys,json;print(json.load(sys.stdin)["resourceGroupName"]["value"])')"
APP_FQDN="$(echo "$OUTPUTS"   | python3 -c 'import sys,json;print(json.load(sys.stdin)["appFqdn"]["value"])')"
APP_NAME="$(echo "$OUTPUTS"   | python3 -c 'import sys,json;print(json.load(sys.stdin)["appName"]["value"])')"
KV_NAME="$(echo "$OUTPUTS"    | python3 -c 'import sys,json;print(json.load(sys.stdin)["keyVaultName"]["value"])')"
# v1.0.0: frontend Container App is the sole viewer path after v1.10.0
# SWA decommission.
FRONTEND_APP_NAME="$(echo "$OUTPUTS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["frontendAppName"]["value"])')"
FRONTEND_URL="$(echo "$OUTPUTS"      | python3 -c 'import sys,json;print(json.load(sys.stdin)["frontendUrl"]["value"])')"

echo "    resource group:  $RG_OUT"
echo "    app FQDN:        https://$APP_FQDN"
echo "    key vault:       $KV_NAME"
echo "    viewer:          $FRONTEND_URL"

# v1.28.1: defensive cleanup of the rtd-<env>-nsg left behind by v1.28.0.
# The NSG was ineffective (Container Apps' shared LB SNATs external
# traffic, so subnet NSG rules can't filter by real client IP) and
# v1.28.1's Bicep no longer declares it or attaches it to the subnet —
# but Bicep is upsert, not full desired-state, so the existing NSG isn't
# auto-deleted. Detach from the subnet first (idempotent no-op if
# Bicep already cleared it), then delete the NSG. Both silent when
# nothing to do.
LEGACY_NSG_NAME="rtd-${ENV_NAME}-nsg"
if az network nsg show -g "$RG_OUT" -n "$LEGACY_NSG_NAME" --only-show-errors -o none 2>/dev/null; then
    az network vnet subnet update -g "$RG_OUT" \
        --vnet-name "rtd-${ENV_NAME}-vnet" -n container-apps \
        --network-security-group "" --only-show-errors -o none 2>/dev/null || true
    if az network nsg delete -g "$RG_OUT" -n "$LEGACY_NSG_NAME" \
        --only-show-errors -o none 2>/dev/null; then
        blue "    v1.28.1 cleanup: removed legacy $LEGACY_NSG_NAME (NSG couldn't filter external client IPs on Container Apps envs)"
    fi
fi

# ---------------------------------------------------------------------------
# Wait for backend health
# The backend startup command runs `alembic upgrade head` before uvicorn
# starts, so by the time /health returns green the schema is initialized.
# The first revision also races KV identity propagation — bump it so the
# second revision picks up the now-propagated managed identity role.
# ---------------------------------------------------------------------------

bold "[5/6] Forcing fresh revision + waiting for the app to come healthy…"
echo "    (migrations run automatically on startup; waiting for schema + DB to be ready)"

REV_BUMP="$(date +%s)"
az containerapp update -n "$APP_NAME" -g "$RG_OUT" \
    --container-name backend \
    --set-env-vars "RTD_REVISION_BUMP=$REV_BUMP" --only-show-errors -o none

for i in {1..40}; do
    if curl -sf "https://$APP_FQDN/health" >/dev/null 2>&1; then
        green "    app is up — schema initialized."
        break
    fi
    [[ $i -eq 40 ]] && die "app never became healthy. Check: az containerapp logs show -n $APP_NAME -g $RG_OUT --container backend"
    sleep 6
done

# ---------------------------------------------------------------------------
# v1.10.0: SWA build + deploy step removed. The frontend Container App
# (Bicep module modules/frontend.bicep) is now the sole viewer. Its
# ingress IP restrictions + Entra env vars were stamped during the Bicep
# deploy at step [3/6]; no post-Bicep viewer work required.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Bootstrap — mint admin key + store secrets in Key Vault
# ---------------------------------------------------------------------------

bold "[6/6] Bootstrapping — minting admin API key and storing secrets…"

# Grant the logged-in user Key Vault Secrets Officer so we can write secrets.
# Subscription Owner doesn't auto-inherit KV data-plane in RBAC mode.
ME="$(az ad signed-in-user show --query id -o tsv)"
KV_ID="$(az keyvault show -n "$KV_NAME" -g "$RG_OUT" --query id -o tsv)"
az role assignment create \
    --role "Key Vault Secrets Officer" \
    --assignee "$ME" \
    --scope "$KV_ID" \
    --only-show-errors -o none 2>/dev/null || true   # idempotent — ignore if already assigned
echo "    waiting for KV role to propagate…"
sleep 30

# Mint the bootstrap admin key. Token prints to stdout — we ALSO capture
# it via grep so we can auto-seed both admin-api-key and worker-mcp-api-key
# into Key Vault without an interactive paste. Bicep unconditionally resets
# both secrets to placeholders every deploy (see keyvault.bicep), so this
# step MUST re-seed them or the worker's MCP client hits 401 on every tool
# call (surfaced as "mcp transport error" in the Tactical agent step log).
#
# v1.4.6: mint name is timestamp-suffixed so the ``already exists`` guard
# in mint_api_key doesn't kill re-installs. Prior versions used a fixed
# ``bootstrap`` name that made the mint fail on the second install of an
# env, silently skipping the auto-seed and leaving worker-mcp-api-key at
# the placeholder — the very bug this whole flow is fixing.
_MINT_NAME="bootstrap-$(date -u +%Y%m%d-%H%M%S)"
echo
blue "    Minting bootstrap admin API key (name=$_MINT_NAME) — token prints below AND is auto-seeded to KV:"
echo
_MINT_OUTPUT="$(container_exec "python -m app.scripts.mint_api_key --name $_MINT_NAME --scope admin" 2>&1)"
printf '%s\n' "$_MINT_OUTPUT"
_MINTED_TOKEN="$(printf '%s' "$_MINT_OUTPUT" | grep -oE 'rtd_[A-Za-z0-9_-]+' | tail -1)"
echo

if [[ -n "$_MINTED_TOKEN" ]]; then
    az keyvault secret set --vault-name "$KV_NAME" --name admin-api-key \
        --value "$_MINTED_TOKEN" --only-show-errors -o none
    green "    admin-api-key auto-seeded to Key Vault."
    az keyvault secret set --vault-name "$KV_NAME" --name worker-mcp-api-key \
        --value "$_MINTED_TOKEN" --only-show-errors -o none
    green "    worker-mcp-api-key auto-seeded to Key Vault (fixes silent tool-call 401)."
elif [[ "$NON_INTERACTIVE" != "true" ]]; then
    red "    couldn't auto-capture the minted token — falling back to interactive paste."
    read -rsp "    Paste the rtd_… token to store it in Key Vault (hidden): " ADMIN_KEY
    echo
    if [[ -n "$ADMIN_KEY" ]]; then
        az keyvault secret set --vault-name "$KV_NAME" --name admin-api-key \
            --value "$ADMIN_KEY" --only-show-errors -o none
        az keyvault secret set --vault-name "$KV_NAME" --name worker-mcp-api-key \
            --value "$ADMIN_KEY" --only-show-errors -o none
        green "    admin-api-key + worker-mcp-api-key stored in Key Vault."
    else
        red "    no token provided — seed both manually:"
        red "      az keyvault secret set --vault-name $KV_NAME --name admin-api-key --value '<token>'"
        red "      az keyvault secret set --vault-name $KV_NAME --name worker-mcp-api-key --value '<token>'"
    fi
else
    red "    FAILED to auto-capture the minted token in non-interactive mode."
    red "    Tool calls will 401 until you seed worker-mcp-api-key manually:"
    red "      az keyvault secret set --vault-name $KV_NAME --name worker-mcp-api-key --value '<rtd_… token>'"
fi

# Store LLM keys
if [[ -n "$ANTHROPIC_KEY" ]]; then
    az keyvault secret set --vault-name "$KV_NAME" --name anthropic-api-key \
        --value "$ANTHROPIC_KEY" --only-show-errors -o none
    green "    anthropic-api-key stored in Key Vault."
fi
if [[ -n "$OPENAI_KEY" ]]; then
    az keyvault secret set --vault-name "$KV_NAME" --name openai-api-key \
        --value "$OPENAI_KEY" --only-show-errors -o none
    green "    openai-api-key stored in Key Vault."
fi
if [[ -n "$PLANNER_KEY" ]]; then
    az keyvault secret set --vault-name "$KV_NAME" --name planner-api-key \
        --value "$PLANNER_KEY" --only-show-errors -o none
    green "    planner-api-key stored in Key Vault."
fi

# v1.4.7: force a NEW revision so the containers re-read Key Vault secrets.
#
# Why not `revision restart`: Azure Container Apps bakes secret values into
# each replica's env vars AT REVISION CREATION TIME. A restart re-launches
# the container process but reuses the same baked env — a KV secret update
# in the previous step goes ignored until a new revision is minted. That's
# exactly what bit the worker-mcp-api-key seed for the whole 1.4.x line
# before this fix — the KV secret said rtd_..., but the running container's
# WORKER_MCP_API_KEY env var was still the stale placeholder.
#
# `--revision-suffix` forces a fresh revision even when the config hasn't
# structurally changed. Suffix pattern is lowercase alnum + hyphens; a
# UTC timestamp guarantees uniqueness across re-runs.
bold "Forcing a new revision so containers pull fresh Key Vault secrets…"
_REV_SUFFIX="postseed-$(date -u +%Y%m%d%H%M%S)"
az containerapp update -n "$APP_NAME" -g "$RG_OUT" \
    --revision-suffix "$_REV_SUFFIX" --only-show-errors -o none
green "    new revision '$_REV_SUFFIX' minted; old revision drains automatically."

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
green "Deploy complete."
echo
echo "  API URL:         https://$APP_FQDN"
echo "  Viewer:          $FRONTEND_URL"
echo "  Resource group:  $RG_OUT"
echo "  Key Vault:       $KV_NAME"
echo "  Tenant:          $TENANT_ID"
echo

ENC_URL="$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1], safe=''))" "https://$APP_FQDN")"
ENC_NAME="$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1], safe=''))" "$ENV_NAME")"
echo "Quick-start link for your teammate (pre-fills the source form):"
blue "  $FRONTEND_URL/sources?url=$ENC_URL&name=$ENC_NAME"
echo

echo "Mint a scoped key for each analyst:"
echo "  az containerapp exec -n $APP_NAME -g $RG_OUT --container backend \\"
echo "      --command 'python -m app.scripts.mint_api_key --name <name> --scope cli'"
echo

if [[ -z "$ANTHROPIC_KEY" && -z "$OPENAI_KEY" ]]; then
    red "  No LLM key was stored. Runs will fail until you add one:"
    echo "  az keyvault secret set --vault-name $KV_NAME --name anthropic-api-key --value 'sk-ant-…'"
    echo "  Then restart: az containerapp revision restart -n $APP_NAME -g $RG_OUT --revision \$(az containerapp revision list -n $APP_NAME -g $RG_OUT --query '[0].name' -o tsv)"
fi

echo
bold "Connect Claude Code (MCP) — paste your rtd_… token from step 6:"
blue "  claude mcp add rtd-${ENV_NAME} \\"
blue "      --transport sse \\"
blue "      --url https://$APP_FQDN/mcp/sse \\"
blue "      --header 'X-API-Key: <your-rtd-token>'"
echo "  Then: claude  (start a session and ask 'What engagements do I have?')"
