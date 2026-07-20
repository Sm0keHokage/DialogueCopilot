"""EventSub WebSocket listener (FR-11, FR-14, IR-21, NFR-Rel-04).

Protocol: connect -> session_welcome (session_id) -> create subscription via
Helix under the reader's user token -> notifications / keepalives. On
session_reconnect we follow the new URL; on any failure we reconnect with
exponential backoff capped by `eventsub.reconnect_max_backoff_s`.

AR-07: message shapes follow the Twitch docs at implementation time.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from .helix import HelixClient

log = logging.getLogger(__name__)

OnEvent = Callable[[dict[str, Any]], Awaitable[Any]]
OnStatus = Callable[[str, str | None], Awaitable[None]]
TokenProvider = Callable[[], Awaitable[str]]


class EventSubListener:
    def __init__(
        self,
        *,
        ws_url: str,
        helix: HelixClient,
        token_provider: TokenProvider,
        broadcaster_user_id: str,
        reader_user_id: str,
        on_event: OnEvent,
        on_status: OnStatus,
        max_backoff_s: int = 30,
        keepalive_grace_s: int = 15,
    ) -> None:
        self._ws_url = ws_url
        self._helix = helix
        self._token_provider = token_provider
        self._broadcaster_user_id = broadcaster_user_id
        self._reader_user_id = reader_user_id
        self._on_event = on_event
        self._on_status = on_status
        self._max_backoff_s = max_backoff_s
        self._keepalive_grace_s = keepalive_grace_s

    async def run(self) -> None:
        backoff = 1.0
        url = self._ws_url
        reconnecting = False
        while True:
            try:
                async with websockets.connect(url, max_size=2**20) as ws:
                    welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    session = welcome.get("payload", {}).get("session", {})
                    session_id = session.get("id", "")
                    keepalive = int(session.get("keepalive_timeout_seconds") or 10)
                    if not reconnecting:
                        token = await self._token_provider()
                        await self._helix.create_chat_message_subscription(
                            token, self._broadcaster_user_id, self._reader_user_id, session_id
                        )
                    await self._on_status("active", None)
                    backoff = 1.0
                    reconnecting = False
                    next_url = await self._read_loop(ws, keepalive)
                    if next_url is None:
                        url = self._ws_url
                    else:
                        url = next_url
                        reconnecting = True  # keep existing subscription on Twitch-driven move
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any transport/subscribe error => retry
                log.warning("eventsub connection error: %s", type(exc).__name__)
                await self._on_status("error", type(exc).__name__)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, float(self._max_backoff_s))
                url = self._ws_url
                reconnecting = False

    async def _read_loop(self, ws: Any, keepalive_s: int) -> str | None:
        """Returns a reconnect URL if Twitch asked us to move, else None."""
        timeout = keepalive_s + self._keepalive_grace_s
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            msg = json.loads(raw)
            mtype = msg.get("metadata", {}).get("message_type", "")
            if mtype == "notification":
                event = msg.get("payload", {}).get("event") or {}
                await self._on_event(event)
            elif mtype == "session_reconnect":
                new_url = msg.get("payload", {}).get("session", {}).get("reconnect_url")
                if new_url:
                    return str(new_url)
            elif mtype == "revocation":
                await self._on_status("error", "subscription revoked")
                return None
            # session_keepalive: nothing to do, the recv timeout is the watchdog
