"""RBAC dependencies (FR-01, FR-02, NFR-Sec-05): every endpoint declares its role."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from .errors import ApiError
from .sessions import SessionData

ROLE_OWNER = "owner"
ROLE_MODERATOR = "moderator"


@dataclass
class AuthContext:
    user_id: int
    channel_id: int
    role: str
    twitch_user_id: str
    login: str
    session: SessionData


async def load_session(request: Request) -> SessionData | None:
    store = request.app.state.sessions
    cookie = request.cookies.get(request.app.state.settings.session_cookie_name)
    return await store.load(cookie)


async def require_auth(request: Request) -> AuthContext:
    sess = await load_session(request)
    if sess is None or "user_id" not in sess.data:
        raise ApiError(401, "unauthorized", "Authentication required")
    d = sess.data
    return AuthContext(
        user_id=int(d["user_id"]),
        channel_id=int(d["channel_id"]),
        role=str(d["role"]),
        twitch_user_id=str(d.get("twitch_user_id", "")),
        login=str(d.get("login", "")),
        session=sess,
    )


async def require_channel_member(channel_id: int, request: Request) -> AuthContext:
    """Owner or moderator of this specific channel."""
    ctx = await require_auth(request)
    if ctx.channel_id != channel_id or ctx.role not in (ROLE_OWNER, ROLE_MODERATOR):
        raise ApiError(403, "forbidden", "No access to this channel")
    return ctx


async def require_channel_owner(channel_id: int, request: Request) -> AuthContext:
    ctx = await require_auth(request)
    if ctx.channel_id != channel_id or ctx.role != ROLE_OWNER:
        raise ApiError(403, "forbidden", "Owner role required")
    return ctx
