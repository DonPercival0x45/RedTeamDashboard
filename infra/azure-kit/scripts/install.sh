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
# Comma-separated list of IPv4 CIDRs the SWA will accept browser traffic
# from. Empty → no restriction (wide open). Resolution precedence (highest
# first), all evaluated post-Bicep:
#   1. --allowed-ips flag (explicit; empty value clears the lock)
#   2. SWA Environment Variables blade (Azure Portal → Static Web App →
#      Settings → Environment variables → RTD_VIEWER_ALLOWED_IPS)
#   3. RTD_VIEWER_ALLOWED_IPS shell env
# Whatever resolves is written BACK to the SWA env vars at the end so the
# next install picks it up automatically — set once in the Portal, install
# many times. Standard SKU is required for the IP block to take effect.
#
# The same precedence applies to RTD_ENTRA_TENANT_ID and RTD_ENTRA_CLIENT_ID:
# pass --entra-tenant-id / --entra-client-id once, and every subsequent
# install resolves them from the SWA blade so the analyst's SSO doesn't
# silently regress to "analyst@localhost" if a teammate runs install.sh
# without the flags (see the v0.7.0 fix for the 2026-06-30 5qprod gotcha).
ALLOWED_IPS="${RTD_VIEWER_ALLOWED_IPS:-}"
ALLOWED_IPS_FROM_FLAG=false
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
  --entra-tenant-id ID    Entra tenant id for analyst SSO (from setup-entra.sh).
                          Optional. Persists to the SWA's Environment Variables
                          blade (RTD_ENTRA_TENANT_ID) for subsequent installs.
                          Falls back to: SWA env vars, then shell RTD_ENTRA_TENANT_ID.
  --entra-client-id ID    Entra app (client) id for analyst SSO.
                          Optional. Persists to the SWA's Environment Variables
                          blade (RTD_ENTRA_CLIENT_ID) for subsequent installs.
                          Falls back to: SWA env vars, then shell RTD_ENTRA_CLIENT_ID.
  --allowed-ips CSV       Comma-separated IPv4 CIDRs the viewer SWA accepts
                          browser traffic from (e.g. '1.2.3.4/32,5.6.7.8/32').
                          Empty → no IP restriction. Persists to the SWA's
                          Azure-side Environment Variables blade for the
                          next install. Falls back to: SWA env vars (if
                          already set), then shell RTD_VIEWER_ALLOWED_IPS.
                          Standard SKU required.
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

bold "[1/7] Checking prerequisites…"
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
    bold "[2/7] Generated postgres password (stored in Key Vault as 'postgres-password')."
else
    bold "[2/7] Using provided postgres password."
fi

# ---------------------------------------------------------------------------
# Pre-Bicep: resolve Entra IDs from the existing SWA's Environment
# Variables blade if no CLI flag was passed. The Bicep deploy below stamps
# whatever values we have into the backend Container App's env (which gate
# ``settings.entra_enabled`` server-side — empty values silently drop every
# Bearer token to a "header required" 401, the v0.7.1 prod bug). This
# resolve MUST run BEFORE the Bicep deploy so the backend gets the right
# values; the post-deploy block further down does the same thing for the
# viewer build / write-back path but lands too late for the backend.
#
# On a first install the SWA doesn't exist yet, the query 404s, and we
# fall through to whatever the CLI flags / shell env provided. No-op.
SWA_PREDICTED_NAME="rtd-${ENV_NAME}-viewer"
if [[ "$ENTRA_TENANT_ID_FROM_FLAG" != "true" ]]; then
    PRE_STORED_TENANT="$(az staticwebapp appsettings list \
        -n "$SWA_PREDICTED_NAME" -g "$RG_NAME" \
        --query "properties.RTD_ENTRA_TENANT_ID" -o tsv 2>/dev/null || true)"
    if [[ -n "$PRE_STORED_TENANT" && "$PRE_STORED_TENANT" != "None" ]]; then
        ENTRA_TENANT_ID="$PRE_STORED_TENANT"
        blue "    Entra tenant id pre-resolved from SWA Environment Variables"
    fi
fi
if [[ "$ENTRA_CLIENT_ID_FROM_FLAG" != "true" ]]; then
    PRE_STORED_CLIENT="$(az staticwebapp appsettings list \
        -n "$SWA_PREDICTED_NAME" -g "$RG_NAME" \
        --query "properties.RTD_ENTRA_CLIENT_ID" -o tsv 2>/dev/null || true)"
    if [[ -n "$PRE_STORED_CLIENT" && "$PRE_STORED_CLIENT" != "None" ]]; then
        ENTRA_CLIENT_ID="$PRE_STORED_CLIENT"
        blue "    Entra client id pre-resolved from SWA Environment Variables"
    fi
fi

# v1.0.0: same precedence for the IP allowlist. The Bicep frontend module
# needs it BEFORE the Container App is created (ipSecurityRestrictions are
# ingress config). If the flag wasn't passed, read from the SWA blade —
# same source of truth as the SWA path uses further down.
if [[ "$ALLOWED_IPS_FROM_FLAG" != "true" ]]; then
    PRE_STORED_IPS="$(az staticwebapp appsettings list \
        -n "$SWA_PREDICTED_NAME" -g "$RG_NAME" \
        --query "properties.RTD_VIEWER_ALLOWED_IPS" -o tsv 2>/dev/null || true)"
    if [[ -n "$PRE_STORED_IPS" && "$PRE_STORED_IPS" != "None" ]]; then
        ALLOWED_IPS="$PRE_STORED_IPS"
        blue "    IP allowlist pre-resolved from SWA Environment Variables"
    fi
fi

# ---------------------------------------------------------------------------
# Bicep deploy
# ---------------------------------------------------------------------------

bold "[3/7] Running Bicep deploy '$DEPLOY_NAME'… (5-10 minutes for first run)"

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
    --parameters frontendAllowedIps="$ALLOWED_IPS" \
    --only-show-errors \
    -o none

# ---------------------------------------------------------------------------
# Pull outputs
# ---------------------------------------------------------------------------

bold "[4/7] Capturing deployment outputs…"

OUTPUTS="$(az deployment sub show -n "$DEPLOY_NAME" --query properties.outputs -o json)"

RG_OUT="$(echo "$OUTPUTS"     | python3 -c 'import sys,json;print(json.load(sys.stdin)["resourceGroupName"]["value"])')"
APP_FQDN="$(echo "$OUTPUTS"   | python3 -c 'import sys,json;print(json.load(sys.stdin)["appFqdn"]["value"])')"
APP_NAME="$(echo "$OUTPUTS"   | python3 -c 'import sys,json;print(json.load(sys.stdin)["appName"]["value"])')"
KV_NAME="$(echo "$OUTPUTS"    | python3 -c 'import sys,json;print(json.load(sys.stdin)["keyVaultName"]["value"])')"
VIEWER_NAME="$(echo "$OUTPUTS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["viewerName"]["value"])')"
VIEWER_URL="$(echo "$OUTPUTS"  | python3 -c 'import sys,json;print(json.load(sys.stdin)["viewerUrl"]["value"])')"
# v1.0.0: the new Container App viewer. Runs alongside the SWA during the
# parallel week; after decommission this is the only viewer.
FRONTEND_APP_NAME="$(echo "$OUTPUTS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["frontendAppName"]["value"])')"
FRONTEND_URL="$(echo "$OUTPUTS"      | python3 -c 'import sys,json;print(json.load(sys.stdin)["frontendUrl"]["value"])')"

echo "    resource group:  $RG_OUT"
echo "    app FQDN:        https://$APP_FQDN"
echo "    key vault:       $KV_NAME"
echo "    viewer (SWA):    $VIEWER_URL"
echo "    viewer (v1.0):   $FRONTEND_URL"

# ---------------------------------------------------------------------------
# Wait for backend health
# The backend startup command runs `alembic upgrade head` before uvicorn
# starts, so by the time /health returns green the schema is initialized.
# The first revision also races KV identity propagation — bump it so the
# second revision picks up the now-propagated managed identity role.
# ---------------------------------------------------------------------------

bold "[5/7] Forcing fresh revision + waiting for the app to come healthy…"
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
# Deploy the viewer static bundle to the Static Web App
# ---------------------------------------------------------------------------

bold "[5.5/7] Building + deploying viewer to Static Web App…"
SWA_SKIPPED=false
FRONTEND_DIR="$(cd "$KIT_ROOT/../.." && pwd)/frontend"
if ! command -v docker >/dev/null 2>&1; then
    red "    skipped — docker not on PATH; install Docker then re-run"
    red "    or deploy manually: SWA name=$VIEWER_NAME, see docs/DEPLOY.md"
    SWA_SKIPPED=true
elif [[ ! -d "$FRONTEND_DIR" ]]; then
    red "    skipped — viewer source not found at $FRONTEND_DIR"
    red "    (run install.sh from inside a repo checkout). See docs/DEPLOY.md"
    SWA_SKIPPED=true
else
    ENTRA_SCOPE=""
    [[ -n "$ENTRA_CLIENT_ID" ]] && ENTRA_SCOPE="api://$ENTRA_CLIENT_ID/access_as_user"
    [[ -n "$ENTRA_CLIENT_ID" ]] && SSO_STATE="on" || SSO_STATE="off (API-key auth)"
    TMP_DIR="$(mktemp -d)"
    tar -C "$FRONTEND_DIR" --exclude=node_modules --exclude=.next --exclude=out -cf - . \
        | tar -C "$TMP_DIR" -xf -

    # Resolve the IP allowlist BEFORE the build so the substitution can use
    # the right value. Precedence: --allowed-ips flag wins; otherwise pull
    # from the SWA's Azure-side Environment Variables (set in Portal or by
    # a previous install). Shell env was already captured into ALLOWED_IPS
    # at startup and is the floor.
    if [[ "$ALLOWED_IPS_FROM_FLAG" != "true" ]]; then
        SWA_STORED_IPS="$(az staticwebapp appsettings list \
            -n "$VIEWER_NAME" -g "$RG_OUT" \
            --query "properties.RTD_VIEWER_ALLOWED_IPS" -o tsv 2>/dev/null || true)"
        if [[ -n "$SWA_STORED_IPS" && "$SWA_STORED_IPS" != "None" ]]; then
            ALLOWED_IPS="$SWA_STORED_IPS"
            blue "    IP allowlist sourced from SWA Environment Variables"
        fi
    fi

    # Same precedence for the Entra IDs — read from SWA Environment Variables
    # if no CLI flag was passed. Without this, an operator running install.sh
    # without --entra-* drops the viewer back to "analyst@localhost" because
    # ENTRA_ENABLED in the bundle goes false (tenantId/clientId empty).
    if [[ "$ENTRA_TENANT_ID_FROM_FLAG" != "true" ]]; then
        SWA_STORED_TENANT="$(az staticwebapp appsettings list \
            -n "$VIEWER_NAME" -g "$RG_OUT" \
            --query "properties.RTD_ENTRA_TENANT_ID" -o tsv 2>/dev/null || true)"
        if [[ -n "$SWA_STORED_TENANT" && "$SWA_STORED_TENANT" != "None" ]]; then
            ENTRA_TENANT_ID="$SWA_STORED_TENANT"
            blue "    Entra tenant id sourced from SWA Environment Variables"
        fi
    fi
    if [[ "$ENTRA_CLIENT_ID_FROM_FLAG" != "true" ]]; then
        SWA_STORED_CLIENT="$(az staticwebapp appsettings list \
            -n "$VIEWER_NAME" -g "$RG_OUT" \
            --query "properties.RTD_ENTRA_CLIENT_ID" -o tsv 2>/dev/null || true)"
        if [[ -n "$SWA_STORED_CLIENT" && "$SWA_STORED_CLIENT" != "None" ]]; then
            ENTRA_CLIENT_ID="$SWA_STORED_CLIENT"
            blue "    Entra client id sourced from SWA Environment Variables"
        fi
    fi
    # Refresh the derived scope + SSO state in case the SWA-stored values
    # changed what we resolved at boot (variables set at L265-267).
    ENTRA_SCOPE=""
    [[ -n "$ENTRA_CLIENT_ID" ]] && ENTRA_SCOPE="api://$ENTRA_CLIENT_ID/access_as_user"
    [[ -n "$ENTRA_CLIENT_ID" ]] && SSO_STATE="on" || SSO_STATE="off (API-key auth)"

    # Fetch the last 20 GitHub Releases and stamp them into the viewer's
    # static assets. The What's-New banner reads /releases.json on load to
    # decide whether a new version landed since the analyst last visited.
    # Public repo → no auth header needed. Failure here is non-fatal: the
    # banner just won't show.
    mkdir -p "$TMP_DIR/public"
    if curl -sf -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/${IMAGE_REPO_OWNER}/RedTeamDashboard/releases?per_page=20" \
        -o "$TMP_DIR/public/releases.json"; then
        echo "    fetched releases.json ($(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$TMP_DIR/public/releases.json") entries)"

        # v1.3.0 What's New Cleanup — for each release, resolve the previous
        # tag and pull the commit list between them via /compare, then bucket
        # commit titles by convention (v.../v(...) → feature, fix(...) → fix,
        # qol|perf|refactor|docs → qol, feedback: → dropped, rest → ops).
        # Enriched schema per release adds a ``categories`` object; frontend
        # renders those blocks above the fold. Legacy releases.json (no
        # categories) still renders via the raw-body fallback path — this
        # step is best-effort.
        python3 - "$TMP_DIR/public/releases.json" "${IMAGE_REPO_OWNER}" <<'PY' || red "    (couldn't enrich releases.json with categories — falls back to raw-body render)"
import json
import re
import sys
import urllib.request

path = sys.argv[1]
owner = sys.argv[2]

with open(path) as fh:
    releases = json.load(fh)

if not releases:
    sys.exit(0)


def gh_compare(prev_tag: str, this_tag: str):
    url = (
        f"https://api.github.com/repos/{owner}/RedTeamDashboard/"
        f"compare/{prev_tag}...{this_tag}"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — public GH API
        return json.load(resp)


PR_RE = re.compile(r"\(#(\d+)\)\s*$")
FEATURE_RE = re.compile(r"^v\d+\.\d+\.\d+(\([^)]*\))?:")
FIX_RE = re.compile(r"^fix(\([^)]*\))?:")
QOL_RE = re.compile(r"^(qol|perf|refactor|docs)(\([^)]*\))?:")
HIDDEN_RE = re.compile(r"^feedback:")


def bucket(title: str) -> str | None:
    if HIDDEN_RE.match(title):
        return None
    if FEATURE_RE.match(title):
        return "features"
    if FIX_RE.match(title):
        return "fixes"
    if QOL_RE.match(title):
        return "qol"
    return "ops"


# Releases API returns newest-first. Pair each release with the one right
# after it to get the "previous tag" for compare.
for i, rel in enumerate(releases):
    if "categories" in rel:
        continue  # idempotent — don't re-enrich
    this_tag = rel.get("tag_name")
    if not this_tag:
        continue
    prev_tag = releases[i + 1]["tag_name"] if i + 1 < len(releases) else None
    categories = {"features": [], "fixes": [], "qol": [], "ops": []}
    if prev_tag:
        try:
            data = gh_compare(prev_tag, this_tag)
        except Exception as exc:  # noqa: BLE001 — best-effort enrichment
            print(f"    skip categorize {this_tag} vs {prev_tag}: {exc}", file=sys.stderr)
            rel["categories"] = categories
            continue
        for c in data.get("commits") or []:
            raw_title = (c.get("commit") or {}).get("message") or ""
            title = raw_title.splitlines()[0].strip()
            if not title:
                continue
            b = bucket(title)
            if b is None:
                continue
            pr_match = PR_RE.search(title)
            pr = int(pr_match.group(1)) if pr_match else None
            clean = PR_RE.sub("", title).strip()
            categories[b].append(
                {"title": clean, "sha": (c.get("sha") or "")[:7], "pr": pr}
            )
    rel["categories"] = categories

with open(path, "w") as fh:
    json.dump(releases, fh)

print(
    f"    enriched releases.json — categorized {sum(1 for r in releases if r.get('categories'))} release(s)"
)
PY
    else
        red "    couldn't fetch GitHub releases — viewer ships with empty release list"
        echo "[]" > "$TMP_DIR/public/releases.json"
    fi

    echo "    building viewer (API=https://$APP_FQDN, SSO=$SSO_STATE)…"
    if docker run --rm \
        -e NEXT_OUTPUT=export \
        -e NEXT_PUBLIC_API_BASE_URL="https://$APP_FQDN" \
        -e NEXT_PUBLIC_ENTRA_TENANT_ID="$ENTRA_TENANT_ID" \
        -e NEXT_PUBLIC_ENTRA_CLIENT_ID="$ENTRA_CLIENT_ID" \
        -e NEXT_PUBLIC_ENTRA_API_SCOPE="$ENTRA_SCOPE" \
        -v "$TMP_DIR:/app" -w /app node:lts \
        sh -c "npm ci --no-audit --no-fund && npm run build"; then
        # Stamp staticwebapp.config.json into the built output. The
        # template lives in the repo (frontend/staticwebapp.config.json.template);
        # IPs are deploy-time params. Empty IP list → networking block
        # omitted (wide open). Standard SKU honors allowedIpRanges; on
        # Free SKU the block is ignored.
        if [[ -f "$TMP_DIR/staticwebapp.config.json.template" ]]; then
            echo "    writing staticwebapp.config.json (allowed-ips=${ALLOWED_IPS:-<none>})…"
            python3 - "$TMP_DIR/staticwebapp.config.json.template" "$TMP_DIR/out/staticwebapp.config.json" "$ALLOWED_IPS" <<'PY'
import json, sys
template_path, out_path, ips_csv = sys.argv[1], sys.argv[2], sys.argv[3]
with open(template_path) as f:
    config = json.load(f)
# Strip the explanatory $comment field — SWA ignores it but no need to ship.
config.pop("$comment", None)
ips = [ip.strip() for ip in ips_csv.split(",") if ip.strip()]
if ips:
    config["networking"] = {"allowedIpRanges": ips}
with open(out_path, "w") as f:
    json.dump(config, f, indent=2)
PY
            if [[ -n "$ALLOWED_IPS" ]]; then
                green "    IP allowlist: $ALLOWED_IPS"
            else
                blue "    no --allowed-ips supplied; SWA stays open to all IPs"
            fi
        else
            red "    template missing at $TMP_DIR/staticwebapp.config.json.template — skipping IP gating"
        fi
        DEPLOY_TOKEN="$(az staticwebapp secrets list -n "$VIEWER_NAME" -g "$RG_OUT" --query 'properties.apiKey' -o tsv)"
        docker run --rm -v "$TMP_DIR/out:/work" node:lts sh -c \
            "cd /tmp && SWA_CLI_TELEMETRY_OPTOUT=1 npx -y @azure/static-web-apps-cli@latest \
                deploy /work --deployment-token $DEPLOY_TOKEN \
                --env production --no-use-keychain"
        green "    viewer deployed."

        # Persist the resolved IP allowlist back to the SWA's Environment
        # Variables blade so the next install (by anyone with az access)
        # inherits it without needing the flag. Operators can also edit
        # via the Portal directly. Setting to empty clears the lock.
        if az staticwebapp appsettings set \
            -n "$VIEWER_NAME" -g "$RG_OUT" \
            --setting-names "RTD_VIEWER_ALLOWED_IPS=$ALLOWED_IPS" \
            --only-show-errors -o none 2>/dev/null; then
            if [[ -n "$ALLOWED_IPS" ]]; then
                green "    SWA app settings: RTD_VIEWER_ALLOWED_IPS=$ALLOWED_IPS"
            else
                blue "    SWA app settings: RTD_VIEWER_ALLOWED_IPS cleared (open allowlist)"
            fi
        else
            red "    couldn't write RTD_VIEWER_ALLOWED_IPS to SWA app settings — "
            red "    next install will need --allowed-ips again. Set it manually in "
            red "    Portal → $VIEWER_NAME → Settings → Environment variables."
        fi

        # Same idea for the Entra IDs — persist them so re-installs don't
        # need --entra-tenant-id / --entra-client-id again. Empty values
        # are passed through too (clears the SSO if someone explicitly
        # wants to turn it off via a future install).
        if az staticwebapp appsettings set \
            -n "$VIEWER_NAME" -g "$RG_OUT" \
            --setting-names \
                "RTD_ENTRA_TENANT_ID=$ENTRA_TENANT_ID" \
                "RTD_ENTRA_CLIENT_ID=$ENTRA_CLIENT_ID" \
            --only-show-errors -o none 2>/dev/null; then
            if [[ -n "$ENTRA_TENANT_ID" && -n "$ENTRA_CLIENT_ID" ]]; then
                green "    SWA app settings: RTD_ENTRA_{TENANT,CLIENT}_ID persisted"
            else
                blue "    SWA app settings: RTD_ENTRA_{TENANT,CLIENT}_ID cleared (SSO off)"
            fi
        else
            red "    couldn't write RTD_ENTRA_* to SWA app settings — "
            red "    next install will need --entra-tenant-id / --entra-client-id"
            red "    flags again to preserve SSO. Set them manually in"
            red "    Portal → $VIEWER_NAME → Settings → Environment variables."
        fi
    else
        red "    viewer build failed — see output above. SWA left empty."
        SWA_SKIPPED=true
    fi
    docker run --rm -v "$TMP_DIR:/t" node:lts chown -R "$(id -u):$(id -g)" /t || true
    rm -rf "$TMP_DIR"
fi

# ---------------------------------------------------------------------------
# Bootstrap — mint admin key + store secrets in Key Vault
# ---------------------------------------------------------------------------

bold "[6/7] Bootstrapping — minting admin API key and storing secrets…"

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

# Mint the bootstrap admin key. The token prints to stdout — copy it.
echo
blue "    Minting bootstrap admin API key — COPY the rtd_… token that appears below:"
echo
container_exec 'python -m app.scripts.mint_api_key --name bootstrap --scope admin'
echo

if [[ "$NON_INTERACTIVE" != "true" ]]; then
    read -rsp "    Paste the rtd_… token to store it in Key Vault (hidden): " ADMIN_KEY
    echo
    if [[ -n "$ADMIN_KEY" ]]; then
        az keyvault secret set --vault-name "$KV_NAME" --name admin-api-key \
            --value "$ADMIN_KEY" --only-show-errors -o none
        green "    admin-api-key stored in Key Vault."
    else
        red "    no token provided — store it manually: az keyvault secret set --vault-name $KV_NAME --name admin-api-key --value '<token>'"
    fi
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

# Restart so the app picks up the newly stored secrets
bold "[7/7] Restarting app to pick up Key Vault secrets…"
REV="$(az containerapp revision list -n "$APP_NAME" -g "$RG_OUT" \
    --query '[?properties.active].name | [0]' -o tsv)"
az containerapp revision restart -n "$APP_NAME" -g "$RG_OUT" \
    --revision "$REV" --only-show-errors -o none
green "    restart triggered."

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
green "Deploy complete."
echo
echo "  API URL:         https://$APP_FQDN"
echo "  Viewer (SWA):    $VIEWER_URL"
echo "  Viewer (v1.0.0): $FRONTEND_URL"
echo "  Resource group:  $RG_OUT"
echo "  Key Vault:       $KV_NAME"
echo "  Tenant:          $TENANT_ID"
echo
# v1.0.0 parallel-week note: both viewers accept the same Entra sign-in
# and talk to the same backend. Once the SWA is decommissioned, drop
# viewer.bicep + this SWA banner and the Container App becomes the only
# viewer. See docs/V1_CUTOVER_CHECKLIST.md.
echo "Parallel week: both viewers are live and equivalent. See docs/V1_CUTOVER_CHECKLIST.md."
echo

if [[ "$SWA_SKIPPED" != "true" ]]; then
    ENC_URL="$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1], safe=''))" "https://$APP_FQDN")"
    ENC_NAME="$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1], safe=''))" "$ENV_NAME")"
    echo "Quick-start link for your teammate (pre-fills the source form):"
    blue "  $VIEWER_URL/sources?url=$ENC_URL&name=$ENC_NAME"
    echo
fi

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
