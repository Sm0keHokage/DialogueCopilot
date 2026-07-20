"""Realtime hub: pushes flag/status events to open admin sessions (FR-33, IR-17)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)


class Hub:
    def __init__(self) -> None:
        self._channels: dict[int, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, channel_id: int, ws: WebSocket) -> None:
        async with self._lock:
            self._channels[channel_id].add(ws)

    async def disconnect(self, channel_id: int, ws: WebSocket) -> None:
        async with self._lock:
            self._channels[channel_id].discard(ws)

    async def broadcast(self, channel_id: int, event_type: str, data: Any) -> None:
        conns = list(self._channels.get(channel_id, ()))
        if not conns:
            return
        payload = json.dumps({"type": event_type, "data": data}, default=str, ensure_ascii=False)
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 - dead connection, drop it
                await self.disconnect(channel_id, ws)
