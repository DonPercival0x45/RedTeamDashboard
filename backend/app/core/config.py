from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"
    database_url: str = "postgresql+psycopg://rtd:rtd@postgres:5432/rtd"
    redis_url: str = "redis://redis:6379/0"
    # Base URL the worker uses to reach the MCP server from inside the
    # container. Stamped onto the worker envelope so the Execution Agent
    # knows where to connect with its X-Lease-Token. Override in prod to
    # the public hostname (e.g. https://<app>.azurecontainerapps.io).
    public_base_url: str = "http://backend:8000"

    # API key the worker uses to authenticate to the MCP server when
    # executing every run (Stage 3+1: the local-registry fallback was
    # ripped). REQUIRED — the worker fails fast at boot if this is blank.
    # Provision once per deployment with a cli-scoped key, stash in KV
    # as ``worker-mcp-api-key``, surface as this env var.
    worker_mcp_api_key: str = ""

    # How often the worker sweeps expired MCP leases (active rows past
    # ``expires_at``) into status=expired. The per-request
    # ``validate_token`` already rejects expired leases at the MCP server,
    # so this is for accounting cleanliness — the Costs and lease-state
    # views show fewer stale "active" rows. 5 minutes balances DB chatter
    # against UI freshness; override via env if needed.
    lease_sweep_interval: int = 300

    # Stage 2 — isolated MCP via a secondary Azure Container App with
    # scale-to-zero. ACA Jobs don't accept HTTP ingress, so the ephemeral
    # MCP host is a second Container App provisioned alongside the main
    # one: ingress on /mcp, scale 0..1, idle = $0. When the column
    # ``mcp_leases.requires_container`` is True, Tactical stamps this
    # App's URL on the worker envelope instead of the colocated one. When
    # ``aca_mcp_app_enabled`` is False (the default — and forced in
    # local-dev), every lease falls back to colocated regardless.
    aca_mcp_app_enabled: bool = False
    # FQDN of the secondary MCP App, populated by deploy from the Bicep
    # output. Example: "https://rtd-mcp.<env>.azurecontainerapps.io".
    # Tactical appends ``/mcp`` itself.
    aca_mcp_url: str = ""

    # CORS allow-origins for the browser viewer. Defaults cover local dev.
    # Kit deploys override this with the central viewer's origin (Phase 6)
    # so a browser there can call this tenant's API directly.
    #
    # NoDecode tells pydantic-settings *not* to JSON-decode the env var
    # before the validator runs — without it, `list[str]` types are
    # parsed as JSON first and a plain CSV value blows up.
    cors_allow_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("["):
                import json

                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return v

    # ── Microsoft Entra ID (per-analyst SSO) ─────────────────────────────
    # When tenant + client id are set, the API additionally accepts
    # `Authorization: Bearer <jwt>` access tokens issued by this Entra app
    # (validated against the tenant JWKS), resolving the caller to a User by
    # the token's `oid`. Left blank → Entra auth is disabled and local dev
    # relies on X-API-Key / X-User-Id. The API-key path always remains for
    # the CLI regardless.
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    # Expected access-token audience. Blank → defaults to api://<client_id>.
    entra_audience: str = ""

    @property
    def entra_enabled(self) -> bool:
        return bool(self.entra_tenant_id and self.entra_client_id)

    @property
    def entra_expected_audience(self) -> str:
        if self.entra_audience:
            return self.entra_audience
        return f"api://{self.entra_client_id}" if self.entra_client_id else ""

    @property
    def entra_issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/v2.0"

    @property
    def entra_jwks_uri(self) -> str:
        return (
            f"https://login.microsoftonline.com/{self.entra_tenant_id}"
            "/discovery/v2.0/keys"
        )

    # Default LLM backend when a run doesn't specify one.
    # - "anthropic" → Claude API (paid, requires ANTHROPIC_API_KEY)
    # - "openai"    → OpenAI API (paid, requires OPENAI_API_KEY)
    # - "ollama"    → Local Ollama (free, runs as a compose service)
    # - "azure"     → Azure OpenAI (production target)
    llm_provider: str = "anthropic"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-7"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Ollama
    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "llama3.1:8b"

    # Azure OpenAI (production target — populate from Key Vault on AKS)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"

    # Azure Blob Storage for engagement exports (archive / flush)
    # Set AZURE_STORAGE_ACCOUNT_NAME to enable; unset → exports returned inline only.
    azure_storage_account_name: str = ""
    azure_storage_container_name: str = "engagement-exports"

    # v1.3.1: GitHub repo used by the What's New / releases feed. The
    # ``/releases.json`` endpoint fetches from
    # ``api.github.com/repos/<github_repo>/releases`` + runs the same
    # categorization enricher install.sh writes into the static bundle,
    # caches for ``releases_cache_ttl_seconds``. Overridable per-deploy
    # via env ``RTD_GITHUB_REPO`` if a fork wants to point at a
    # different origin.
    github_repo: str = "DonPercival0x45/RedTeamDashboard"
    releases_cache_ttl_seconds: int = 3600

    # v0.12.0 — Tools tab sandbox runner selection.
    # ``docker`` = LocalDockerRunner (mounts /var/run/docker.sock, used
    # in local dev + CI). ``aci`` = ACIRunner (Azure Container Instances
    # via managed identity, used in prod). Set via env RTD_SANDBOX_RUNNER.
    sandbox_runner: str = "docker"
    # Azure Files share used by ACIRunner to hand source into the
    # spawned container. Populated by Bicep in v0.12+ prod installs.
    # Format: "<share-name>" on ``azure_storage_account_name``.
    aci_source_share: str = "tool-sources"
    # Azure resource group + subscription that the backend's managed
    # identity spawns ACIs into. In prod the backend has Container
    # Instance Contributor on this RG. Local dev never touches these.
    aci_subscription_id: str = ""
    aci_resource_group: str = ""
    aci_location: str = "centralus"

    # BYO provider keys are now ephemeral — stored in Redis under a per-
    # user hash with a sliding TTL, never persisted at rest. The Fernet
    # master key field below is retained ONLY for one release so prior
    # deploys can still import the module; nothing reads it anymore and
    # the next release deletes it. New TTL knob controls how long an
    # uploaded key survives idle.
    provider_key_master: str = "ZmVybmV0LWRldi1ub3QtZm9yLXByb2QtMzJieXRlc18="
    # Sliding TTL on the per-user Redis hash holding the analyst's BYO
    # keys. Refreshed on every read or write. 30 min default — short
    # enough that an unattended browser doesn't leave keys reachable;
    # long enough that an active analyst session doesn't constantly
    # re-prompt for a re-upload.
    provider_key_ttl_seconds: int = 1800


settings = Settings()
