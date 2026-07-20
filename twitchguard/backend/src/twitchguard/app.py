"""Application factory: state wiring, middleware, routers, worker lifecycle."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import redis.asyncio as redis_asyncio
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from .api import account, auth, dashboard, flags, moderators, rules, stream
from .api import settings as settings_api
from .config import AppConfig, Settings, load_app_config
from .crypto import TokenCipher
from .db import create_db_engine, create_sessionmaker, init_models
from .emailer import Emailer
from .errors import install_error_handlers
from .logging_setup import register_secret, setup_logging
from .models import Channel
from .pipelines import start_channel_pipeline, token_refresher_loop
from .ratelimit import install_rate_limit
from .sessions import SessionStore
from .supervisor import Supervisor
from .twitch.helix import HelixClient
from .twitch.oauth import TwitchOAuth
from .ws import Hub


def create_app(
    settings: Settings | None = None,
    config: AppConfig | None = None,
    *,
    engine: AsyncEngine | None = None,
    redis_client: Any = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    settings = settings or Settings()
    config = config or load_app_config(settings.config_file)
    setup_logging(settings.log_level)
    # NFR-Sec-03: literal secrets are masked in every log line.
    register_secret(settings.twitch_client_secret)
    register_secret(settings.encryption_key)
    register_secret(settings.session_secret)
    register_secret(settings.smtp_password)

    app = FastAPI(title="TwitchGuard", version="0.1.0", lifespan=_lifespan)
    state = app.state
    state.settings = settings
    state.config = config
    state.cipher = TokenCipher(settings.encryption_key)
    state.engine = engine or create_db_engine(settings.database_url)
    state.sessionmaker = create_sessionmaker(state.engine)
    state.redis = redis_client or redis_asyncio.from_url(
        settings.redis_url, decode_responses=True
    )
    state.http = http_client or httpx.AsyncClient(timeout=config.classifier.api_timeout_s)
    state.sessions = SessionStore(
        state.redis,
        settings.session_secret,
        config.security.session_ttl_s,
        sliding=config.security.session_sliding_renewal,
    )
    state.hub = Hub()
    state.supervisor = Supervisor()
    state.oauth = TwitchOAuth(
        state.http,
        settings.twitch_id_base_url,
        settings.twitch_client_id,
        settings.twitch_client_secret,
        settings.twitch_redirect_uri,
    )
    state.helix = HelixClient(state.http, settings.helix_base_url, settings.twitch_client_id)
    state.emailer = Emailer(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_rate_limit(app)
    install_error_handlers(app)

    @app.middleware("http")
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Baseline hardening headers on every response; /account and /auth
        additionally opt out of caching since they carry session-sensitive
        payloads (nonce codes, account details, tokens)."""
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith(("/account", "/auth")):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(account.router)
    app.include_router(auth.router)
    app.include_router(rules.router)
    app.include_router(flags.router)
    app.include_router(settings_api.router)
    app.include_router(moderators.router)
    app.include_router(dashboard.router)
    app.include_router(stream.router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = app.state
    if state.settings.db_create_all:
        await init_models(state.engine)
    if state.settings.start_workers:
        state.supervisor.start("token_refresher", lambda: token_refresher_loop(state))
        async with state.sessionmaker() as db:
            channels = list(
                (
                    await db.execute(select(Channel).where(Channel.needs_reauth.is_(False)))
                ).scalars()
            )
        for channel in channels:
            if channel.encrypted_access_token:
                start_channel_pipeline(state, channel)
    yield
    await state.supervisor.stop_all()
    await state.http.aclose()
    try:
        await state.redis.aclose()
    except Exception:  # noqa: BLE001 - fake clients may not implement aclose
        pass
    await state.engine.dispose()
