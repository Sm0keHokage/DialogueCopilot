"""Helix API client (IR-18, IR-22).

Deliberately narrow surface: EventSub subscription management and the two
moderation calls used by Action Proxy. There is **no** method for sending chat
messages — the system never writes to chat (§1.3, AR-02, FR-03).

AR-07: payload shapes follow the official Twitch docs as of implementation
time; verify against https://dev.twitch.tv/docs before upgrading.
"""
from __future__ import annotations

from typing import Any

import httpx


class HelixError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"helix {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class HelixClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, client_id: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._client_id = client_id

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Client-Id": self._client_id}

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            return str(body.get("message") or body.get("error") or resp.text[:200])
        except Exception:  # noqa: BLE001
            return resp.text[:200]

    async def create_chat_message_subscription(
        self, token: str, broadcaster_user_id: str, user_id: str, session_id: str
    ) -> str:
        """Subscribe to channel.chat.message v1 over the WebSocket transport (IR-21)."""
        resp = await self._http.post(
            f"{self._base}/eventsub/subscriptions",
            headers=self._headers(token),
            json={
                "type": "channel.chat.message",
                "version": "1",
                "condition": {
                    "broadcaster_user_id": broadcaster_user_id,
                    "user_id": user_id,
                },
                "transport": {"method": "websocket", "session_id": session_id},
            },
        )
        if resp.status_code not in (200, 202):
            raise HelixError(resp.status_code, self._error_message(resp))
        data = resp.json().get("data") or [{}]
        return str(data[0].get("id", ""))

    async def delete_chat_message(
        self, token: str, broadcaster_id: str, moderator_id: str, message_id: str
    ) -> None:
        """IR-18: `delete` -> DELETE /helix/moderation/chat."""
        resp = await self._http.delete(
            f"{self._base}/moderation/chat",
            headers=self._headers(token),
            params={
                "broadcaster_id": broadcaster_id,
                "moderator_id": moderator_id,
                "message_id": message_id,
            },
        )
        if resp.status_code not in (200, 204):
            raise HelixError(resp.status_code, self._error_message(resp))

    async def ban_user(
        self,
        token: str,
        broadcaster_id: str,
        moderator_id: str,
        user_id: str,
        duration_s: int | None = None,
        reason: str = "",
    ) -> None:
        """IR-18: `timeout`/`ban` -> POST /helix/moderation/bans (duration => timeout)."""
        data: dict[str, Any] = {"user_id": user_id, "reason": reason[:500]}
        if duration_s is not None:
            data["duration"] = duration_s
        resp = await self._http.post(
            f"{self._base}/moderation/bans",
            headers=self._headers(token),
            params={"broadcaster_id": broadcaster_id, "moderator_id": moderator_id},
            json={"data": data},
        )
        if resp.status_code != 200:
            raise HelixError(resp.status_code, self._error_message(resp))
