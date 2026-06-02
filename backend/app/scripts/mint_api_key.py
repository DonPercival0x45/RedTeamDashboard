"""Bootstrap API key minter for the deployment installer.

Called once by the kit after ``alembic upgrade head``, before the backend
container starts accepting traffic, to mint the first ``admin`` key. The kit
stores the printed token in the user's Key Vault and surfaces it on stdout
("write this down — you can't read it again").

Usage (inside the backend container)::

    python -m app.scripts.mint_api_key \
        --name "bootstrap" \
        --scope admin

Idempotent on ``--name``: if a key by that name already exists and is not
revoked, the script refuses (exit 1) rather than minting a duplicate. Pick a
different name or revoke the old one first.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.api.api_keys import _generate_key
from app.api.deps import hash_api_key
from app.db.session import SessionLocal
from app.models import APIKey, APIKeyScope


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True, help="Human label for the key.")
    parser.add_argument(
        "--scope",
        required=True,
        choices=[s.value for s in APIKeyScope],
        help="Privilege tier: viewer / cli / admin.",
    )
    return parser.parse_args(argv)


def mint(name: str, scope: APIKeyScope) -> str:
    """Mint a key, persist it, return the plaintext token.

    Raises ``RuntimeError`` if an active key with the same ``name`` exists.
    """
    session = SessionLocal()
    try:
        existing = session.execute(
            select(APIKey).where(
                APIKey.name == name, APIKey.revoked_at.is_(None)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise RuntimeError(
                f"an active API key named {name!r} already exists "
                f"(id={existing.id}); revoke it or pick a different name"
            )

        raw = _generate_key()
        row = APIKey(
            name=name,
            key_hash=hash_api_key(raw),
            scope=scope,
            created_by=None,  # bootstrap key has no user
        )
        session.add(row)
        session.commit()
        return raw
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        token = mint(args.name, APIKeyScope(args.scope))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # Print on its own line so the kit's installer can grep the last line.
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
