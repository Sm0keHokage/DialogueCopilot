"""Per-channel worker wiring: EventSub listener + classifier + token refresher."""
from __future__ import annotations

import asyncio
import functools
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from .audit import record
from .db import as_utc, utcnow
from .ingest import handle_chat_event
from .models import Channel, User
from .moderation.engine import classifier_loop, consumer_name
from .twitch.eventsub import EventSubListener
from .twitch.oauth import TwitchAuthError

log = logging.getLogger(__name__)


def eventsub_task_name(channel_id: int) -> str:
    return f"eventsub:{channel_id}"


def classifier_task_prefix(channel_id: int) -> str:
    return f"classifier:{channel_id}:"


def classifier_task_name(channel_id: int, worker: int) -> str:
    return f"{classifier_task_prefix(channel_id)}{worker}"


def effective_workers(app_state: Any, channel: Channel) -> int:
    """Clamp the per-channel agent count to [1, classifier.max_workers]."""
    configured = int(getattr(channel, "classifier_workers", 1) or 1)
    return max(1, min(configured, app_state.config.classifier.max_workers))


async def _channel_token(app_state: Any, channel_id: int) -> str:
    """Fresh reader token for EventSub subscription (refresh if needed, FR-08)."""
    async with app_state.sessionmaker() as db:
        channel = (
            await db.execute(select(Channel).where(Channel.id == channel_id))
        ).scalar_one()
        expires = as_utc(channel.token_expires_at)
        if expires is not None and expires <= utcnow() and channel.encrypted_refresh_token:
            tokens = await app_state.oauth.refresh(
                app_state.cipher.decrypt(channel.encrypted_refresh_token)
            )
            channel.encrypted_access_token = app_state.cipher.encrypt(tokens.access_token)
            if tokens.refresh_token:
                channel.encrypted_refresh_token = app_state.cipher.encrypt(tokens.refresh_token)
            channel.token_expires_at = datetime.fromtimestamp(
                utcnow().timestamp() + tokens.expires_in, tz=UTC
            )
            await db.commit()
        return app_state.cipher.decrypt(channel.encrypted_access_token)


async def _set_eventsub_status(
    app_state: Any, channel_id: int, status: str, detail: str | None
) -> None:
    async with app_state.sessionmaker() as db:
        channel = (
            await db.execute(select(Channel).where(Channel.id == channel_id))
        ).scalar_one_or_none()
        if channel is None:
            return
        channel.eventsub_status = status
        await db.commit()
    await app_state.hub.broadcast(
        channel_id, "channel.status", {"eventsub_status": status, "detail": detail}
    )


async def eventsub_loop(app_state: Any, channel_id: int, twitch_user_id: str) -> None:
    listener = EventSubListener(
        ws_url=app_state.config.eventsub.websocket_url,
        helix=app_state.helix,
        token_provider=functools.partial(_channel_token, app_state, channel_id),
        broadcaster_user_id=twitch_user_id,
        reader_user_id=twitch_user_id,
        on_event=functools.partial(
            handle_chat_event, app_state.redis, app_state.hub, app_state.config, channel_id
        ),
        on_status=functools.partial(_set_eventsub_status, app_state, channel_id),
        max_backoff_s=app_state.config.eventsub.reconnect_max_backoff_s,
        keepalive_grace_s=app_state.config.eventsub.keepalive_grace_s,
    )
    await listener.run()


def start_classifier_workers(app_state: Any, channel: Channel) -> int:
    """Spawn the configured number of parallel AI agents for the channel."""
    workers = effective_workers(app_state, channel)
    for n in range(1, workers + 1):
        app_state.supervisor.start(
            classifier_task_name(channel.id, n),
            functools.partial(classifier_loop, app_state, channel.id, consumer_name(n)),
        )
    return workers


async def restart_classifier_workers(app_state: Any, channel: Channel) -> int:
    """Hot-apply a new agent count without touching the EventSub listener."""
    await app_state.supervisor.stop_prefix(classifier_task_prefix(channel.id))
    return start_classifier_workers(app_state, channel)


def start_channel_pipeline(app_state: Any, channel: Channel) -> None:
    """FR-11/FR-14: listener + classifier agents per connected channel, supervised."""
    start_classifier_workers(app_state, channel)
    app_state.supervisor.start(
        eventsub_task_name(channel.id),
        functools.partial(eventsub_loop, app_state, channel.id, channel.twitch_user_id),
    )


async def stop_channel_pipeline(app_state: Any, channel_id: int) -> None:
    await app_state.supervisor.stop(eventsub_task_name(channel_id))
    await app_state.supervisor.stop_prefix(classifier_task_prefix(channel_id))


async def token_refresher_loop(app_state: Any) -> None:
    """FR-08/NFR-Rel-04: refresh access tokens before expiry; flag dead refresh tokens."""
    while True:
        await asyncio.sleep(60)
        deadline = utcnow().timestamp() + 300
        async with app_state.sessionmaker() as db:
            channels = list(
                (await db.execute(select(Channel).where(Channel.needs_reauth.is_(False))))
                .scalars()
            )
            for channel in channels:
                if not channel.encrypted_refresh_token:
                    continue
                expires = as_utc(channel.token_expires_at)
                if expires is not None and expires.timestamp() > deadline:
                    continue
                try:
                    tokens = await app_state.oauth.refresh(
                        app_state.cipher.decrypt(channel.encrypted_refresh_token)
                    )
                except TwitchAuthError:
                    # FR-08: invalid refresh token -> "reconnect required".
                    channel.needs_reauth = True
                    channel.eventsub_status = "error"
                    await record(
                        db,
                        channel_id=channel.id,
                        actor_type="system",
                        action="channel.reauth_required",
                    )
                    await app_state.hub.broadcast(
                        channel.id,
                        "channel.status",
                        {"eventsub_status": "error", "needs_reauth": True},
                    )
                    continue
                except Exception as exc:  # noqa: BLE001 - network error, try next round
                    log.warning("token refresh failed for channel %s: %r", channel.id, exc)
                    continue
                channel.encrypted_access_token = app_state.cipher.encrypt(tokens.access_token)
                if tokens.refresh_token:
                    channel.encrypted_refresh_token = app_state.cipher.encrypt(
                        tokens.refresh_token
                    )
                channel.token_expires_at = datetime.fromtimestamp(
                    utcnow().timestamp() + tokens.expires_in, tz=UTC
                )
                users = list(
                    (
                        await db.execute(select(User).where(User.channel_id == channel.id))
                    ).scalars()
                )
                for user in users:
                    expires_u = as_utc(user.token_expires_at)
                    if (
                        user.encrypted_refresh_token
                        and expires_u is not None
                        and expires_u.timestamp() <= deadline
                    ):
                        try:
                            utokens = await app_state.oauth.refresh(
                                app_state.cipher.decrypt(user.encrypted_refresh_token)
                            )
                        except TwitchAuthError:
                            user.encrypted_access_token = None
                            continue
                        except Exception:  # noqa: BLE001
                            continue
                        user.encrypted_access_token = app_state.cipher.encrypt(
                            utokens.access_token
                        )
                        if utokens.refresh_token:
                            user.encrypted_refresh_token = app_state.cipher.encrypt(
                                utokens.refresh_token
                            )
                        user.token_expires_at = datetime.fromtimestamp(
                            utcnow().timestamp() + utokens.expires_in, tz=UTC
                        )
            await db.commit()
