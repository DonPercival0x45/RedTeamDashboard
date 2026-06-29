"""Discord gateway consumer — inbound side of the Feedback bridge.

Connects to Discord with the configured bot token, listens for new
messages in the configured channel, and turns each one into a
``RoadmapSuggestion`` row via the shared service. Gracefully no-ops
when:

- discord.py isn't installed (kept optional so plain installs work)
- No Discord integration row exists in the DB
- The integration row is disabled or missing the bot_token / channel_id

Lives as a daemon thread alongside the other worker consumers
(strategic, lease-sweeper). Restart the worker after changing the
integration row to pick up new config — we don't hot-reload.
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from typing import Any

import redis as redis_lib
import structlog
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import IntegrationType
from app.services import integrations as integration_svc
from app.services import roadmap_suggestions as suggestion_svc

logger = structlog.get_logger(__name__)

SessionFactory = Callable[[], Session]


class DiscordBotThread:
    """Wraps a discord.py client in a daemon thread.

    Initialisation order:

    1. Check that discord.py imports — if not, log + bail.
    2. Read Discord integration row — if missing/disabled, log + bail.
    3. Spin discord.py's asyncio loop in this thread; client.run() is
       blocking, so the thread sits here for the worker's lifetime.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        redis_client: redis_lib.Redis,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis_client

    def _load_config(self) -> dict[str, Any] | None:
        session = self._session_factory()
        try:
            row = integration_svc.get_by_type(session, IntegrationType.discord)
            if row is None or not row.enabled:
                return None
            return {
                "config": dict(row.config or {}),
                "actor_user_id": row.created_by_user_id,
            }
        finally:
            session.close()

    def run(self, stop_event: threading.Event) -> None:
        try:
            import discord  # noqa: F401 — checked import only
        except ImportError:
            logger.info("discord_bot.skipped — discord.py not installed")
            return

        loaded = self._load_config()
        if loaded is None:
            logger.info("discord_bot.skipped — no enabled Discord integration")
            return

        cfg = loaded["config"]
        actor_user_id = loaded["actor_user_id"]
        token = cfg.get("bot_token")
        channel_id_raw = cfg.get("channel_id")
        if not token or not channel_id_raw:
            logger.warning(
                "discord_bot.skipped — bot_token or channel_id missing"
            )
            return
        try:
            channel_id = int(channel_id_raw)
        except (TypeError, ValueError):
            logger.warning(
                "discord_bot.skipped — channel_id not an integer",
                value=str(channel_id_raw),
            )
            return
        if actor_user_id is None:
            logger.warning(
                "discord_bot.skipped — integration has no created_by_user_id; "
                "delete + re-create from the UI so we know whose BYO key to use"
            )
            return

        logger.info(
            "discord_bot.starting",
            channel_id=channel_id,
            env=settings.env,
        )

        # Build the client in this thread's loop. discord.py expects to
        # own the event loop; we run it inline via asyncio.run().
        try:
            asyncio.run(
                _run_client(
                    token=token,
                    channel_id=channel_id,
                    actor_user_id=actor_user_id,
                    session_factory=self._session_factory,
                    redis_client=self._redis,
                    stop_event=stop_event,
                )
            )
        except Exception:
            logger.exception("discord_bot.crashed")


async def _run_client(
    *,
    token: str,
    channel_id: int,
    actor_user_id: Any,
    session_factory: SessionFactory,
    redis_client: redis_lib.Redis,
    stop_event: threading.Event,
) -> None:
    """The asyncio-side of the bot. Sits in the discord.py loop until
    ``stop_event`` flips (checked periodically via a small watchdog)."""
    import discord

    intents = discord.Intents.default()
    intents.message_content = True  # required for message text reads
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        logger.info(
            "discord_bot.ready",
            user=str(client.user),
            channel_id=channel_id,
        )

    @client.event
    async def on_message(message: discord.Message) -> None:
        # Skip our own messages and anything outside the configured channel.
        if message.author == client.user:
            return
        if message.channel.id != channel_id:
            return
        # Skip bot messages (e.g. webhook posts from our OWN outbound
        # notification — webhooks register as bots).
        if message.author.bot:
            return
        body = (message.content or "").strip()
        if len(body) < 4:
            return  # too short to bother evaluating

        author_label = (
            getattr(message.author, "name", None)
            or getattr(message.author, "global_name", None)
            or str(message.author.id)
        )
        source = f"discord:{author_label}"

        session = session_factory()
        try:
            row, execution = suggestion_svc.create_and_evaluate(
                session,
                redis_client,
                author_user_id=actor_user_id,
                body=body,
                source=source,
            )
            session.commit()
            logger.info(
                "discord_bot.relayed",
                suggestion_id=str(row.id),
                execution_status=execution.status.value,
                source=source,
            )
            # Acknowledge in the channel so the poster knows it landed.
            # Reaction perms missing → silently swallow.
            with contextlib.suppress(Exception):
                await message.add_reaction("✅")
        except Exception:
            session.rollback()
            logger.exception(
                "discord_bot.relay_failed",
                source=source,
            )
        finally:
            session.close()

    # Watchdog: poll stop_event so SIGTERM/SIGINT tears the client down.
    async def _watchdog() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(1.0)
        logger.info("discord_bot.stopping")
        await client.close()

    watchdog_task = asyncio.create_task(_watchdog())
    try:
        await client.start(token)
    finally:
        watchdog_task.cancel()
