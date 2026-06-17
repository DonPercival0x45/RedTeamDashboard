"""Fernet-backed encrypt/decrypt for analyst-uploaded API keys.

One master key lives in ``settings.provider_key_master`` (env-injected in
dev, KV secret in prod). We do NOT support per-row keys / per-user keys /
key rotation in this slice — losing the master key means losing every
stored key, which is the intended threat model (operator controls the
master key; analysts trust the operator).

Why Fernet over raw AES-GCM: Fernet packs version + IV + ciphertext + HMAC
in a single urlsafe-base64 token, so a single TEXT column suffices and we
don't have to schema-version the encoding ourselves. The library is
battle-tested and the API surface is two functions.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


class SecretBoxError(RuntimeError):
    """Raised when the master key is misconfigured or a token won't decrypt."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    from app.core.config import settings

    raw = (settings.provider_key_master or "").strip()
    if not raw:
        raise SecretBoxError(
            "PROVIDER_KEY_MASTER is not set. Generate one with "
            "`python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'` and set it via env "
            "(dev) or the KV secret `provider-key-master` (prod)."
        )
    try:
        return Fernet(raw.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise SecretBoxError(
            "PROVIDER_KEY_MASTER must be a urlsafe-base64-encoded 32-byte "
            "Fernet key. Generate one with `Fernet.generate_key()`."
        ) from exc


def encrypt(plaintext: str) -> str:
    """Return the Fernet ciphertext for ``plaintext`` as a urlsafe string."""
    if not plaintext:
        raise SecretBoxError("cannot encrypt empty plaintext")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Return the plaintext for a previously-encrypted token.

    Raises ``SecretBoxError`` if the token is malformed or the master key
    has been rotated since encryption.
    """
    if not ciphertext:
        raise SecretBoxError("cannot decrypt empty ciphertext")
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretBoxError(
            "ciphertext could not be decrypted — master key was likely "
            "rotated since this row was written"
        ) from exc


def last4(plaintext: str) -> str:
    """Return the last 4 chars of ``plaintext`` for UI masking.

    Empty / short keys return an empty string; the caller decides whether
    that's allowed (local-provider rows have no key).
    """
    plaintext = plaintext or ""
    if len(plaintext) < 4:
        return ""
    return plaintext[-4:]


def mask(plaintext: str) -> str:
    """Display string for a key — e.g. ``••••••••••abcd``."""
    tail = last4(plaintext)
    return f"••••••••••{tail}" if tail else "••••••••"


def reset_for_tests() -> None:
    """Drop the cached Fernet so tests can swap the master key per-test."""
    _fernet.cache_clear()
