"""Chat message ingest: EventSub event -> Redis Stream (FR-12, FR-13, FR-15, DR-10).

Raw chat is buffered only in Redis (capped stream + small "recent" list for the
dashboard); nothing is persisted to the database unless a flag is created.
"""
from __future__ import annotations

import json
import time
from typing import Any

from redis.asyncio import Redis

from .config import AppConfig
from .ws import Hub


def stream_key(channel_id: int) -> str:
    return f"tg:stream:{channel_id}"


def recent_key(channel_id: int) -> str:
    return f"tg:recent:{channel_id}"


def dedup_key(channel_id: int, message_id: str) -> str:
    return f"tg:seen:{channel_id}:{message_id}"


async def handle_chat_event(
    redis: Redis, hub: Hub, config: AppConfig, channel_id: int, event: dict[str, Any]
) -> bool:
    """Returns True if the message was enqueued for classification."""
    message_id = str(event.get("message_id") or "")
    text = str((event.get("message") or {}).get("text") or "")
    author_id = str(event.get("chatter_user_id") or "")
    author_login = str(event.get("chatter_user_login") or "")
    # FR-15: system/empty events never reach classification.
    if not message_id or not text.strip() or not author_id:
        return False
    # FR-13: EventSub redelivery dedup by message_id.
    fresh = await redis.set(
        dedup_key(channel_id, message_id), "1", nx=True, ex=config.ingest.dedup_ttl_s
    )
    if not fresh:
        return False
    entry = {
        "message_id": message_id,
        "author_id": author_id,
        "author_login": author_login,
        "text": text,
        "ts_ms": str(int(time.time() * 1000)),
    }
    await redis.xadd(
        stream_key(channel_id),
        entry,  # type: ignore[arg-type]
        maxlen=config.ingest.redis_stream_maxlen,
        approximate=True,
    )
    await redis.lpush(recent_key(channel_id), json.dumps(entry))
    await redis.ltrim(recent_key(channel_id), 0, config.ingest.recent_messages - 1)
    await hub.broadcast(
        channel_id,
        "chat.message",
        {"author_login": author_login, "text": text, "ts_ms": int(entry["ts_ms"])},
    )
    return True
