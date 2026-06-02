from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"
    database_url: str = "postgresql+psycopg://rtd:rtd@postgres:5432/rtd"
    redis_url: str = "redis://redis:6379/0"

    # Which LLM backend default_llm() should construct.
    # - "anthropic" → Claude API (paid, requires ANTHROPIC_API_KEY)
    # - "ollama"    → Local Ollama (free, runs as a compose service)
    # - "azure"     → Azure OpenAI (production target)
    llm_provider: str = "anthropic"

    # Anthropic
    anthropic_model: str = "claude-opus-4-7"

    # Ollama
    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "llama3.1:8b"

    # Azure OpenAI (production target — populate from Key Vault on AKS)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"


settings = Settings()
