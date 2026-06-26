#!/usr/bin/env bash
# Project X-Ray — Deployment Kit teardown.
#
# Deletes the whole resource group (irreversible). Optionally purges the
# Key Vault soft-delete so the name can be reused immediately instead of
# waiting 7 days.
#
# Usage:
#     ./uninstall.sh                       # interactive, default env=prod
#     ./uninstall.sh --env prod --purge    # also purges the KV soft-delete

set -euo pipefail

ENV_NAME="prod"
PURGE_KV=false
NON_INTERACTIVE=false

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  --env NAME    Env name to tear down. Resource group is xray-<env>. (default: prod)
  --purge       Also purge the Key Vault soft-delete so the name is reusable.
  --yes         Skip the confirmation prompt.
  -h, --help    Show this help.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env) ENV_NAME="$2"; shift 2 ;;
        --purge) PURGE_KV=true; shift ;;
        --yes) NON_INTERACTIVE=true; shift ;;
        -h|--help) usage ;;
        *) echo "unknown arg: $1" >&2; usage ;;
    esac
done

RG_NAME="xray-${ENV_NAME}"

red() { printf "\033[31m%s\033[0m\n" "$*"; }

red "About to DELETE resource group '$RG_NAME' and everything in it."
red "Findings, runs, audit logs — all gone. This is irreversible."

if [[ "$NON_INTERACTIVE" != "true" ]]; then
    read -rp "Type the resource group name to confirm: " ack
    [[ "$ack" == "$RG_NAME" ]] || { echo "name did not match — aborted."; exit 1; }
fi

if [[ "$PURGE_KV" == "true" ]]; then
    KV_NAME="$(az keyvault list -g "$RG_NAME" --query "[0].name" -o tsv 2>/dev/null || true)"
fi

echo "Deleting resource group (this can take 5+ minutes)…"
az group delete --name "$RG_NAME" --yes --no-wait

if [[ "$PURGE_KV" == "true" && -n "${KV_NAME:-}" ]]; then
    echo "Purging Key Vault '$KV_NAME' soft-delete…"
    az keyvault purge --name "$KV_NAME" || \
        echo "(Key Vault purge failed — it may already be gone, or wait for the group delete to complete and retry.)"
fi

echo "Done. The group delete is running in the background; check status with:"
echo "    az group show -n $RG_NAME -o table"
