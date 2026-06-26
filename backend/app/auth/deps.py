"""Re-export auth dependencies for domain-local use.
Thin shim so domain files can do: from app.auth.deps import CurrentUser
instead of from app.api.deps import CurrentUser
"""
from app.api.deps import (  # noqa: F401
    CurrentAPIKey,
    CurrentUser,
    DbSession,
    RequireScope,
    hash_api_key,
)
