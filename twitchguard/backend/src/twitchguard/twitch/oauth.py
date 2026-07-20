"""Twitch OAuth Authorization Code Flow (FR-04..FR-09, IR-20, NFR-Sec-01).

The only authentication path in the system. There is no code anywhere that
accepts a Twitch password or a 2FA code (AR-01): credentials are entered on
Twitch's own pages, we only ever see the authorization `code`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

# FR-05: minimal read scopes at connect time...
READ_SCOPES = ["user:read:chat", "channel:bot"]
# ...moderation scopes are requested only when Action Proxy is enabled (§10).
ACTION_SCOPES = ["moderator:manage:banned_users", "moderator:manage:chat_messages"]


class TwitchAuthError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_in: int
    scopes: list[str] = field(default_factory=list)


@dataclass
class TokenIdentity:
    user_id: str
    login: str
    scopes: list[str]


class TwitchOAuth:
    def __init__(
        self, http: httpx.AsyncClient, base_url: str, client_id: str, client_secret: str,
        redirect_uri: str,
    ) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def authorize_url(self, state: str, scopes: list[str]) -> str:
        params = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "scope": " ".join(scopes),
                "state": state,
            }
        )
        return f"{self._base}/oauth2/authorize?{params}"

    async def exchange_code(self, code: str) -> TokenSet:
        resp = await self._http.post(
            f"{self._base}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": self._redirect_uri,
            },
        )
        if resp.status_code != 200:
            raise TwitchAuthError("code_exchange_failed", "Twitch rejected the authorization code")
        return self._token_set(resp.json())

    async def refresh(self, refresh_token: str) -> TokenSet:
        resp = await self._http.post(
            f"{self._base}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": refresh_token,
            },
        )
        if resp.status_code != 200:
            raise TwitchAuthError("invalid_refresh_token", "Refresh token is no longer valid")
        return self._token_set(resp.json())

    async def revoke(self, token: str) -> None:
        resp = await self._http.post(
            f"{self._base}/oauth2/revoke",
            data={"client_id": self._client_id, "token": token},
        )
        if resp.status_code >= 500:
            raise TwitchAuthError("revoke_failed", "Twitch revoke endpoint failed")

    async def validate(self, access_token: str) -> TokenIdentity:
        resp = await self._http.get(
            f"{self._base}/oauth2/validate",
            headers={"Authorization": f"OAuth {access_token}"},
        )
        if resp.status_code != 200:
            raise TwitchAuthError("invalid_token", "Access token failed validation")
        data = resp.json()
        return TokenIdentity(
            user_id=str(data.get("user_id", "")),
            login=str(data.get("login", "")),
            scopes=list(data.get("scopes") or []),
        )

    @staticmethod
    def _token_set(data: dict[str, object]) -> TokenSet:
        scopes_raw = data.get("scope") or []
        if isinstance(scopes_raw, str):
            scopes = scopes_raw.split()
        elif isinstance(scopes_raw, list):
            scopes = [str(s) for s in scopes_raw]
        else:
            scopes = []
        expires_raw = data.get("expires_in", 3600)
        expires_in = int(expires_raw) if isinstance(expires_raw, int | str) else 3600
        return TokenSet(
            access_token=str(data["access_token"]),
            refresh_token=str(data.get("refresh_token", "")),
            expires_in=expires_in or 3600,
            scopes=scopes,
        )
