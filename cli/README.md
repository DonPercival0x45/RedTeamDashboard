# rtd-cli

Command-line client for the [Red Team Dashboard](https://github.com/DonPercival0x45/RedTeamDashboard).

The CLI is the primary way to drive a deployment. The web viewer is read-only;
everything that mutates state (starting runs, deciding approvals, managing
scope, revoking grants) goes through `rtd`.

## Install

```bash
pip install rtd-cli
```

Or from a clone:

```bash
pip install -e ./cli
```

## Quick start

```bash
# 1. Save a profile for your deployment.
rtd login --profile personal \
  --url https://rtd-prod-backend.<hash>.<region>.azurecontainerapps.io \
  --key rtd_<your-admin-key>

# 2. Create an engagement + add scope.
rtd engagement create --name "Acme recon"
rtd engagement scope add acme-recon --kind domain --value acme.com

# 3. Start a run; tail events automatically.
rtd run start acme-recon --prompt "enumerate acme.com subdomains" \
  --provider anthropic --model claude-opus-4-7

# 4. From another shell — approve any active-tool interrupts.
rtd approve <approval_id> --remember
```

## Profiles

Profiles are stored in `~/.config/rtd/config.toml` (0600). One file holds
multiple deployments; the `default` key controls which one un-flagged
commands use.

```toml
default = "personal"

[profile.personal]
url = "https://rtd-prod-backend.purplebeach-xx.centralus.azurecontainerapps.io"
api_key = "rtd_..."

[profile.work]
url = "https://..."
api_key = "rtd_..."
```

Operate the same commands against a different deployment with `--profile`:

```bash
rtd --profile work engagement list
```

## Commands

| Command | Purpose |
|---|---|
| `rtd login` | Save / update a profile and its API key. |
| `rtd profile {list,use,remove}` | Manage saved profiles. |
| `rtd engagement {list,create,view}` | Engagement CRUD. |
| `rtd engagement scope {list,add,remove}` | Scope item management. |
| `rtd run start <slug>` | Kick off a run. Tails events unless `--no-tail`. |
| `rtd tail <slug>` | SSE stream of events. `--thread` to filter. |
| `rtd approve <id>` | Decide a pending approval. `--remember` grants a session auth. |
| `rtd grants {list,revoke}` | Manage per-(engagement, tool) session grants. |
| `rtd findings list <slug>` | Read persisted findings. |
| `rtd ssh <slug>` | Shell into the deployment's backend Container App. |

`rtd ssh` shells out to `az containerapp exec` — you need the Azure CLI
installed and logged into the subscription that hosts the deployment.

## Output formats

Default output is human-readable rich tables. Pass `--json` (global flag) for
machine-parseable output piped through `jq` etc.

```bash
rtd --json engagement list | jq '.[] | .slug'
```

## Security

- The config file is written 0600 on save.
- API keys never appear in `argv` for `rtd ssh` (the key isn't passed to az).
- Every backend call sends `X-API-Key`; profiles aren't cross-contaminated.

## License

MIT — see [LICENSE](../LICENSE) in the repo root.
