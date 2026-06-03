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
#     ./install.sh --env prod --location eastus2 --image-tag v0.1.0
#
# Prereqs (also enforced below):
#   - az logged in: `az login`
#   - az subscription selected: `az account set --subscription rtd-personal`
#   - Bicep CLI installed: `az bicep install`
#   - openssl on PATH (for generating the postgres password if you don't supply one)

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
  --yes                   Skip the confirmation prompt; useful in CI/automation.
  -h, --help              Show this help.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env) ENV_NAME="$2"; shift 2 ;;
        --location) LOCATION="$2"; shift 2 ;;
        --image-repo-owner) IMAGE_REPO_OWNER="$2"; shift 2 ;;
        --image-tag) IMAGE_TAG="$2"; shift 2 ;;
        --llm-provider) LLM_PROVIDER="$2"; shift 2 ;;
        --postgres-password) PG_PW="$2"; shift 2 ;;
        --yes) NON_INTERACTIVE=true; shift ;;
        -h|--help) usage ;;
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
echo

if [[ "$NON_INTERACTIVE" != "true" ]]; then
    read -rp "Proceed with this configuration? [y/N] " ack
    [[ "$ack" =~ ^[Yy]$ ]] || { echo "aborted."; exit 1; }
fi

# ---------------------------------------------------------------------------
# Postgres password
# ---------------------------------------------------------------------------

if [[ -z "$PG_PW" ]]; then
    # 24 url-safe chars; Azure Postgres requires 8-128 with mixed classes,
    # this satisfies it (base64 mix gives upper/lower/digits).
    PG_PW="$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-24)Aa1!"
    bold "[2/6] Generated postgres password (stored in Key Vault as 'postgres-password')."
else
    bold "[2/6] Using provided postgres password."
fi

# ---------------------------------------------------------------------------
# Bicep deploy
# ---------------------------------------------------------------------------

bold "[3/6] Running Bicep deploy '$DEPLOY_NAME'… (5-10 minutes for first run)"

# release.yml strips the `v` prefix when tagging GHCR images
# (`version="${tag#v}"`), so `:v0.2.0` doesn't exist — only `:0.2.0`.
# Normalize so the operator can pass either form. Original IMAGE_TAG is
# preserved for GH-release URL construction below (those keep the `v`).
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
    --only-show-errors \
    -o none

# ---------------------------------------------------------------------------
# Pull outputs
# ---------------------------------------------------------------------------

bold "[4/6] Capturing deployment outputs…"

OUTPUTS="$(az deployment sub show -n "$DEPLOY_NAME" --query properties.outputs -o json)"

RG_OUT="$(echo "$OUTPUTS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["resourceGroupName"]["value"])')"
APP_FQDN="$(echo "$OUTPUTS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["appFqdn"]["value"])')"
APP_NAME="$(echo "$OUTPUTS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["appName"]["value"])')"
KV_NAME="$(echo "$OUTPUTS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["keyVaultName"]["value"])')"
VIEWER_NAME="$(echo "$OUTPUTS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["viewerName"]["value"])')"
VIEWER_URL="$(echo "$OUTPUTS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["viewerUrl"]["value"])')"

echo "    resource group:  $RG_OUT"
echo "    app FQDN:        https://$APP_FQDN"
echo "    key vault:       $KV_NAME"
echo "    viewer URL:      $VIEWER_URL"

# ---------------------------------------------------------------------------
# Wait for backend health
# ---------------------------------------------------------------------------

# Container Apps' first revision races the system-assigned identity's role
# propagation to Entra. The result is "secret capp-<appname> not found" on
# the first revision because KV refs return 403 before the role lands. By
# now (post-Bicep) the role has propagated; force a new revision so it
# refetches secrets with the now-authorized identity.
bold "[5/6] Forcing fresh revision + waiting for the app to come healthy…"
echo "    (the first revision races KV identity propagation; bumping forces a fresh one)"
REV_BUMP="$(date +%s)"
# --container-name required because the app has 3 containers (backend,
# worker, redis); az otherwise refuses to know which container's env to
# mutate. Bumping `backend` is sufficient — the new revision restarts all
# siblings together.
az containerapp update -n "$APP_NAME" -g "$RG_OUT" \
    --container-name backend \
    --set-env-vars "RTD_REVISION_BUMP=$REV_BUMP" --only-show-errors -o none


for i in {1..40}; do
    if curl -sf "https://$APP_FQDN/health" >/dev/null 2>&1; then
        green "    app is up."
        break
    fi
    [[ $i -eq 40 ]] && die "app never became healthy. Check 'az containerapp logs show -n $APP_NAME -g $RG_OUT'."
    sleep 6
done

# ---------------------------------------------------------------------------
# Deploy the viewer static bundle to the Static Web App
# ---------------------------------------------------------------------------
#
# We download the prebuilt viewer bundle from this release's GitHub assets
# and upload it via the SWA deployment token. No npm / build pipeline
# needed on the operator's machine — just `unzip` and the SWA CLI (npx).
#
# Skipped when --image-tag is `latest` and we have no way to know which
# release's bundle to fetch: operator can re-run with --image-tag <ver>
# after picking a version.
bold "[5.5/6] Deploying viewer to Static Web App…"
if ! command -v npx >/dev/null 2>&1; then
    red "    skipped — npx not on PATH; install Node.js 18+ then re-run"
    red "    or deploy manually: SWA name=$VIEWER_NAME, see docs/DEPLOY.md"
    SWA_SKIPPED=true
elif [[ "$IMAGE_TAG" == "latest" ]]; then
    red "    skipped — running with --image-tag latest. Re-run with a pinned"
    red "    version (e.g. --image-tag v0.2.0) to fetch the matching viewer bundle."
    SWA_SKIPPED=true
else
    SWA_SKIPPED=false
    BUNDLE_TAG="$IMAGE_TAG"
    [[ "$BUNDLE_TAG" != v* ]] && BUNDLE_TAG="v$BUNDLE_TAG"
    BUNDLE_URL="https://github.com/DonPercival0x45/RedTeamDashboard/releases/download/$BUNDLE_TAG/rtd-viewer-static-$BUNDLE_TAG.zip"
    TMP_DIR="$(mktemp -d)"
    echo "    downloading $BUNDLE_URL"
    if ! curl -fsSL -o "$TMP_DIR/viewer.zip" "$BUNDLE_URL"; then
        red "    download failed; the release may not have the static bundle yet"
        red "    (only v0.2.0+ ships it). Re-run with a newer --image-tag or"
        red "    deploy the bundle yourself — see docs/DEPLOY.md"
        SWA_SKIPPED=true
    else
        unzip -q "$TMP_DIR/viewer.zip" -d "$TMP_DIR/viewer"
        DEPLOY_TOKEN="$(az staticwebapp secrets list -n "$VIEWER_NAME" -g "$RG_OUT" --query 'properties.apiKey' -o tsv)"
        # SWA CLI ships standalone via npm; npx fetches it on first run.
        SWA_CLI_TELEMETRY_OPTOUT=1 npx -y @azure/static-web-apps-cli@latest \
            deploy "$TMP_DIR/viewer" \
            --deployment-token "$DEPLOY_TOKEN" \
            --env production \
            --no-use-keychain
        green "    viewer deployed."
    fi
    rm -rf "$TMP_DIR"
fi

# ---------------------------------------------------------------------------
# Configure Entra ID sign-in for the Static Web App
# ---------------------------------------------------------------------------
#
# Static Web Apps' auth runtime reads AAD_CLIENT_ID + AAD_CLIENT_SECRET
# from app settings and uses them to broker the Entra login. The kit
# creates a per-tenant app registration scoped to AzureADMyOrg so only
# users in the customer's tenant can sign in (defense-in-depth on top of
# the per-source API key).
#
# Requires the operator to have AAD app-create permission in their tenant
# (default for Members; restricted only when the tenant explicitly locks
# it down). On failure we fall back to manual instructions instead of
# blocking the rest of the install.
bold "[5.6/6] Configuring Entra ID sign-in for the viewer…"

AAD_CONFIGURED=false
if [[ "$SWA_SKIPPED" == "true" ]]; then
    echo "    skipped — viewer wasn't deployed"
else
    AAD_DISPLAY_NAME="rtd-${ENV_NAME}-viewer"
    AAD_REDIRECT_URI="${VIEWER_URL}/.auth/login/aad/callback"

    # Reuse an existing app registration if the operator re-ran install.sh.
    EXISTING_APP_ID="$(az ad app list --display-name "$AAD_DISPLAY_NAME" --query '[0].appId' -o tsv 2>/dev/null || true)"

    if [[ -n "$EXISTING_APP_ID" ]]; then
        echo "    reusing existing app registration '$AAD_DISPLAY_NAME' (appId=$EXISTING_APP_ID)"
        AAD_CLIENT_ID="$EXISTING_APP_ID"
        if ! az ad app update --id "$AAD_CLIENT_ID" \
            --web-redirect-uris "$AAD_REDIRECT_URI" \
            --sign-in-audience AzureADMyOrg \
            --only-show-errors -o none 2>/tmp/aad-err; then
            red "    couldn't update redirect URI on existing app — see /tmp/aad-err"
        fi
    else
        echo "    creating app registration '$AAD_DISPLAY_NAME'…"
        if AAD_CLIENT_ID="$(az ad app create \
            --display-name "$AAD_DISPLAY_NAME" \
            --sign-in-audience AzureADMyOrg \
            --web-redirect-uris "$AAD_REDIRECT_URI" \
            --query 'appId' -o tsv 2>/tmp/aad-err)"; then
            true
        else
            AAD_CLIENT_ID=""
            red "    couldn't create app registration. Your tenant may restrict"
            red "    app-create to admins; ask one to run:"
            blue "        az ad app create --display-name $AAD_DISPLAY_NAME \\"
            blue "            --sign-in-audience AzureADMyOrg \\"
            blue "            --web-redirect-uris $AAD_REDIRECT_URI"
            red "    Then set AAD_CLIENT_ID + AAD_CLIENT_SECRET on the SWA:"
            blue "        az staticwebapp appsettings set -n $VIEWER_NAME -g $RG_OUT \\"
            blue "            --setting-names AAD_CLIENT_ID=<appId> AAD_CLIENT_SECRET=<secret>"
        fi
    fi

    if [[ -n "$AAD_CLIENT_ID" ]]; then
        echo "    rotating client secret (2-year lifetime)…"
        SECRET_LABEL="kit-install-$(date +%s)"
        AAD_CLIENT_SECRET="$(az ad app credential reset \
            --id "$AAD_CLIENT_ID" \
            --display-name "$SECRET_LABEL" \
            --years 2 \
            --query 'password' -o tsv 2>/tmp/aad-err)" || true

        if [[ -n "${AAD_CLIENT_SECRET:-}" ]]; then
            az staticwebapp appsettings set \
                --name "$VIEWER_NAME" \
                --resource-group "$RG_OUT" \
                --setting-names "AAD_CLIENT_ID=$AAD_CLIENT_ID" "AAD_CLIENT_SECRET=$AAD_CLIENT_SECRET" \
                --only-show-errors -o none && AAD_CONFIGURED=true
            if [[ "$AAD_CONFIGURED" == "true" ]]; then
                green "    Entra sign-in wired up (appId=$AAD_CLIENT_ID, tenant-scoped)."
            fi
        else
            red "    couldn't generate client secret — see /tmp/aad-err"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Manual post-deploy steps
# ---------------------------------------------------------------------------

bold "[6/6] One-time manual bootstrap — run these next (cannot be scripted yet — see follow-up):"
echo
blue "  # Apply database migrations"
echo "  az containerapp exec -n $APP_NAME -g $RG_OUT --container backend \\"
echo "      --command 'alembic upgrade head'"
echo
blue "  # Mint the bootstrap admin API key (save the output — it can't be retrieved again)"
echo "  az containerapp exec -n $APP_NAME -g $RG_OUT --container backend \\"
echo "      --command 'python -m app.scripts.mint_api_key --name bootstrap --scope admin'"
echo
blue "  # Stash that key into Key Vault so it's recoverable from the portal"
echo "  az keyvault secret set --vault-name $KV_NAME \\"
echo "      --name admin-api-key --value '<paste-the-rtd_-token-here>'"
echo
blue "  # Drop in your LLM provider key(s) — only the one(s) you'll use"
echo "  az keyvault secret set --vault-name $KV_NAME --name anthropic-api-key  --value 'sk-ant-…'"
echo "  az keyvault secret set --vault-name $KV_NAME --name openai-api-key     --value 'sk-…'"
echo
blue "  # Restart the app so it picks up the rotated secrets"
echo "  az containerapp revision restart -n $APP_NAME -g $RG_OUT \\"
echo "      --revision \$(az containerapp revision list -n $APP_NAME -g $RG_OUT --query '[0].name' -o tsv)"
echo

green "Deploy complete. Summary:"
echo
echo "  API URL:          https://$APP_FQDN"
echo "  Viewer URL:       $VIEWER_URL"
echo "  Resource group:   $RG_OUT"
echo "  Key Vault:        $KV_NAME"
echo "  Tenant:           $TENANT_ID"
echo "  Postgres pw saved in KV at: secret/postgres-password"
echo
if [[ "$SWA_SKIPPED" != "true" ]]; then
    if [[ "$AAD_CONFIGURED" == "true" ]]; then
        echo "Viewer sign-in: Entra ID (scoped to tenant $TENANT_ID — only your"
        echo "                directory members can load the page)."
    else
        echo "Viewer sign-in: NOT configured — see the [5.6] output above for"
        echo "                manual setup steps."
    fi
    echo
    # Magic link: pre-fills the URL + name in the viewer's /sources form
    # so the operator only pastes their API key. Share this with teammates.
    ENC_URL="$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1], safe=''))" "https://$APP_FQDN")"
    ENC_NAME="$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1], safe=''))" "$ENV_NAME")"
    echo "Quick-start link for testers (pre-fills the source form):"
    blue "  $VIEWER_URL/sources?url=$ENC_URL&name=$ENC_NAME"
    echo
    echo "Each tester also needs their own scoped API key — mint one with:"
    echo "  az containerapp exec -n $APP_NAME -g $RG_OUT --container backend \\"
    echo "      --command 'python -m app.scripts.mint_api_key --name <tester> --scope cli'"
else
    echo "Viewer wasn't deployed. Once it's pushed, the magic-link form is at:"
    echo "  $VIEWER_URL/sources?url=https%3A%2F%2F$APP_FQDN&name=$ENV_NAME"
fi
