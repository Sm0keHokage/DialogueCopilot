"""Server-side sessions in Redis, HMAC-signed session-id cookie (FR-10, NFR-Sec-04)."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis


@dataclass
class SessionData:
    sid: str
    data: dict[str, Any]


class SessionStore:
    def __init__(self, redis: Redis, secret: str, ttl_s: int, sliding: bool = True) -> None:
        self._redis = redis
        self._secret = (secret or "twitchguard-dev-secret").encode("utf-8")
        self.ttl_s = ttl_s
        self._sliding = sliding

    def _key(self, sid: str) -> str:
        return f"tg:sess:{sid}"

    def _sign(self, sid: str) -> str:
        return hmac.new(self._secret, sid.encode("ascii"), hashlib.sha256).hexdigest()[:32]

    async def create(self, data: dict[str, Any]) -> str:
        sid = secrets.token_urlsafe(32)
        await self._redis.set(self._key(sid), json.dumps(data), ex=self.ttl_s)
        return f"{sid}.{self._sign(sid)}"

    async def load(self, cookie_value: str | None) -> SessionData | None:
        if not cookie_value or "." not in cookie_value:
            return None
        sid, sig = cookie_value.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(sid)):
            return None
        raw = await self._redis.get(self._key(sid))
        if raw is None:
            return None
        if self._sliding:
            await self._redis.expire(self._key(sid), self.ttl_s)
        return SessionData(sid=sid, data=json.loads(raw))

    async def save(self, sid: str, data: dict[str, Any]) -> None:
        await self._redis.set(self._key(sid), json.dumps(data), ex=self.ttl_s)

    async def delete(self, cookie_value: str | None) -> None:
        if not cookie_value or "." not in cookie_value:
            return
        sid = cookie_value.rsplit(".", 1)[0]
        await self._redis.delete(self._key(sid))
