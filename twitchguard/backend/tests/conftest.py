"""Test fixtures: in-memory SQLite + fakeredis + a mock Twitch/LLM HTTP layer."""
from __future__ import annotations

import urllib.parse
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
from asgi_lifespan import LifespanManager
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, MockTransport
from sqlalchemy import select

from twitchguard.app import create_app
from twitchguard.config import AppConfig, ClassifierConfig, SecurityConfig, Settings
from twitchguard.ingest import handle_chat_event
from twitchguard.models import Channel
from twitchguard.moderation.backends import _EXTRA_BACKENDS
from twitchguard.twitch.oauth import ACTION_SCOPES, READ_SCOPES

RULES_DIR = Path(__file__).resolve().parent.parent / "rules_builtin"


class FakeTwitch:
    """Mock of id.twitch.tv + api.twitch.tv + LLM vendor endpoints."""

    def __init__(self) -> None:
        self.logins: dict[str, dict[str, Any]] = {}  # code -> identity
        self.tokens: dict[str, dict[str, Any]] = {}  # access_token -> identity
        self.revoked: list[str] = []
        self.dead_refresh_tokens: set[str] = set()
        self.eventsub_subs: list[dict[str, Any]] = []
        self.helix_calls: list[dict[str, Any]] = []
        self.helix_error: tuple[int, str] | None = None
        self.llm_status: int = 200
        self._counter = 0

    def add_login(self, code: str, user_id: str, login: str, scopes: list[str]) -> None:
        self.logins[code] = {"user_id": user_id, "login": login, "scopes": scopes}

    def _issue(self, identity: dict[str, Any]) -> dict[str, Any]:
        self._counter += 1
        access = f"acc-{identity['login']}-{self._counter}"
        refresh = f"ref-{identity['login']}-{self._counter}"
        self.tokens[access] = identity
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 3600,
            "scope": identity["scopes"],
            "token_type": "bearer",
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "id.twitch.tv":
            if path == "/oauth2/token":
                form = urllib.parse.parse_qs(request.content.decode())
                grant = form.get("grant_type", [""])[0]
                if grant == "authorization_code":
                    code = form.get("code", [""])[0]
                    identity = self.logins.get(code)
                    if identity is None:
                        return httpx.Response(400, json={"message": "invalid code"})
                    return httpx.Response(200, json=self._issue(identity))
                if grant == "refresh_token":
                    rt = form.get("refresh_token", [""])[0]
                    if rt in self.dead_refresh_tokens:
                        return httpx.Response(400, json={"message": "Invalid refresh token"})
                    login = rt.split("-")[1] if "-" in rt else "owner"
                    identity = next(
                        (i for i in self.logins.values() if i["login"] == login),
                        {"user_id": "0", "login": login, "scopes": []},
                    )
                    return httpx.Response(200, json=self._issue(identity))
            if path == "/oauth2/validate":
                token = request.headers.get("Authorization", "").removeprefix("OAuth ")
                identity = self.tokens.get(token)
                if identity is None:
                    return httpx.Response(401, json={"message": "invalid token"})
                return httpx.Response(200, json=identity)
            if path == "/oauth2/revoke":
                form = urllib.parse.parse_qs(request.content.decode())
                self.revoked.append(form.get("token", [""])[0])
                return httpx.Response(200)
        if host == "api.twitch.tv":
            if path == "/helix/eventsub/subscriptions":
                self.eventsub_subs.append({"headers": dict(request.headers)})
                return httpx.Response(202, json={"data": [{"id": "sub-1"}]})
            if path == "/helix/moderation/chat":
                if self.helix_error:
                    status, msg = self.helix_error
                    return httpx.Response(status, json={"message": msg})
                self.helix_calls.append(
                    {"kind": "delete", "params": dict(request.url.params)}
                )
                return httpx.Response(204)
            if path == "/helix/moderation/bans":
                if self.helix_error:
                    status, msg = self.helix_error
                    return httpx.Response(status, json={"message": msg})
                import json as _json

                self.helix_calls.append(
                    {
                        "kind": "ban",
                        "params": dict(request.url.params),
                        "body": _json.loads(request.content.decode()),
                    }
                )
                return httpx.Response(200, json={"data": [{}]})
        if host in ("api.anthropic.com", "api.openai.com", "api.deepseek.com"):
            if self.llm_status != 200:
                return httpx.Response(self.llm_status, json={"error": "denied"})
            if host == "api.anthropic.com":
                return httpx.Response(
                    200,
                    json={
                        "content": [{"type": "text", "text": "[]"}],
                        "usage": {"input_tokens": 5, "output_tokens": 1},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "[]"}}],
                    "usage": {"total_tokens": 6},
                },
            )
        return httpx.Response(404, json={"message": f"unmocked {host}{path}"})


def make_settings() -> Settings:
    return Settings(
        twitch_client_id="test-client-id",
        twitch_client_secret="test-client-secret-abcdef",
        twitch_redirect_uri="http://localhost:8000/auth/twitch/callback",
        encryption_key=Fernet.generate_key().decode(),
        database_url="sqlite+aiosqlite://",
        redis_url="redis://unused:6379/0",
        session_secret="test-session-secret-xyz",
        session_cookie_secure=False,
        db_create_all=True,
        start_workers=False,
        builtin_rules_dir=str(RULES_DIR),
        config_file="/nonexistent/config.yaml",
        frontend_origin="http://localhost:5173",
    )


def make_config() -> AppConfig:
    return AppConfig(
        classifier=ClassifierConfig(
            batch_size=10,
            batch_window_ms=0,
            max_retries=1,
            cache_ttl_s=60,
            retry_attempts=2,
            backoff_base_s=0.01,
            backoff_max_s=0.05,
            idle_sleep_s=0.01,
        ),
        security=SecurityConfig(rate_limit_per_minute=1000),
    )


def build_app(
    settings: Settings | None = None,
    config: AppConfig | None = None,
    fake_twitch: FakeTwitch | None = None,
) -> tuple[FastAPI, FakeTwitch]:
    fake = fake_twitch or FakeTwitch()
    application = create_app(
        settings or make_settings(),
        config or make_config(),
        redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
        http_client=httpx.AsyncClient(transport=MockTransport(fake.handler)),
    )
    return application, fake


@pytest.fixture
def fake_twitch() -> FakeTwitch:
    return FakeTwitch()


@pytest.fixture
async def app(fake_twitch: FakeTwitch) -> AsyncIterator[FastAPI]:
    application, _ = build_app(fake_twitch=fake_twitch)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def client2(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Second browser (separate cookie jar) — for the moderator."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_backend_registry() -> Any:
    saved = dict(_EXTRA_BACKENDS)
    yield
    _EXTRA_BACKENDS.clear()
    _EXTRA_BACKENDS.update(saved)


async def login(
    client: httpx.AsyncClient,
    fake_twitch: FakeTwitch,
    *,
    code: str = "code-owner",
    user_id: str = "100",
    login_name: str = "owner",
    action_scopes: bool = False,
) -> dict[str, Any]:
    """Run the full OAuth dance against the mocked Twitch; returns /auth/me."""
    scopes = list(READ_SCOPES) + (list(ACTION_SCOPES) if action_scopes else [])
    fake_twitch.add_login(code, user_id, login_name, scopes)
    resp = await client.get(
        "/auth/twitch/login", params={"action_scopes": int(action_scopes)}
    )
    assert resp.status_code == 302
    query = urllib.parse.parse_qs(urllib.parse.urlparse(resp.headers["location"]).query)
    state = query["state"][0]
    resp = await client.get("/auth/twitch/callback", params={"code": code, "state": state})
    assert resp.status_code == 302, resp.text
    me = (await client.get("/auth/me")).json()
    assert me["authenticated"] is True
    return me


async def set_backend_config(app: FastAPI, channel_id: int, config: dict[str, Any]) -> None:
    async with app.state.sessionmaker() as db:
        channel = (
            await db.execute(select(Channel).where(Channel.id == channel_id))
        ).scalar_one()
        channel.backend_config = config
        await db.commit()


async def enqueue_message(
    app: FastAPI,
    channel_id: int,
    message_id: str,
    text: str,
    *,
    author_login: str = "viewer",
    author_id: str = "500",
) -> bool:
    event = {
        "message_id": message_id,
        "chatter_user_id": author_id,
        "chatter_user_login": author_login,
        "message": {"text": text},
    }
    return await handle_chat_event(
        app.state.redis, app.state.hub, app.state.config, channel_id, event
    )
