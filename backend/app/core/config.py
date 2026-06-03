from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"
    database_url: str = "postgresql+psycopg://rtd:rtd@postgres:5432/rtd"
    redis_url: str = "redis://redis:6379/0"

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


settings = Settings()
