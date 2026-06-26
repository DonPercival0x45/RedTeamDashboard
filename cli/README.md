# xray-cli

Command-line client for the [Project X-Ray](https://github.com/DonPercival0x45/ProjectXRay).

The CLI is the primary way to drive a deployment. The web viewer is read-only;
everything that mutates state (starting runs, deciding approvals, managing
scope, revoking grants) goes through `XR`.

## Install

```bash
pip install xray-cli
```

Or from a clone:

```bash
pip install -e ./cli
```

## Quick start

```bash
# 1. Save a profile for your deployment.
XR login --profile personal \
  --url https://xray-prod-backend.<hash>.<region>.azurecontainerapps.io \
  --key xr_<your-admin-key>

# 2. Create an engagement + add scope.
XR engagement create --name "Acme recon"
XR engagement scope add acme-recon --kind domain --value acme.com

# 3. Start a run; tail events automatically.
XR run start acme-recon --prompt "enumerate acme.com subdomains" \
  --provider anthropic --model claude-opus-4-7

# 4. From another shell — approve any active-tool interrupts.
XR approve <approval_id> --remember
```

## Profiles

Profiles are stored in `~/.config/XR/config.toml` (0600). One file holds
multiple deployments; the `default` key controls which one un-flagged
commands use.

```toml
default = "personal"

[profile.personal]
url = "https://xray-prod-backend.purplebeach-xx.centralus.azurecontainerapps.io"
api_key = "xr_..."

[profile.work]
url = "https://..."
api_key = "xr_..."
```

Operate the same commands against a different deployment with `--profile`:

```bash
XR --profile work engagement list
```

## Commands

| Command | Purpose |
|---|---|
| `XR login` | Save / update a profile and its API key. |
| `XR profile {list,use,remove}` | Manage saved profiles. |
| `XR engagement {list,create,view}` | Engagement CRUD. |
| `XR engagement scope {list,add,remove}` | Scope item management. |
| `XR run start <slug>` | Kick off a run. Tails events unless `--no-tail`. |
| `XR tail <slug>` | SSE stream of events. `--thread` to filter. |
| `XR approve <id>` | Decide a pending approval. `--remember` grants a session auth. |
| `XR grants {list,revoke}` | Manage per-(engagement, tool) session grants. |
| `XR findings list <slug>` | Read persisted findings. |
| `XR ssh <slug>` | Shell into the deployment's backend Container App. |

`XR ssh` shells out to `az containerapp exec` — you need the Azure CLI
installed and logged into the subscription that hosts the deployment.

## Output formats

Default output is human-readable rich tables. Pass `--json` (global flag) for
machine-parseable output piped through `jq` etc.

```bash
XR --json engagement list | jq '.[] | .slug'
```

## Security

- The config file is written 0600 on save.
- API keys never appear in `argv` for `XR ssh` (the key isn't passed to az).
- Every backend call sends `X-API-Key`; profiles aren't cross-contaminated.

## License

MIT — see [LICENSE](../LICENSE) in the repo root.
