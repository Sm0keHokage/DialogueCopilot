"""Auth API: Twitch OAuth flow, sessions, disconnect (IR-01..IR-04, UC-01).

There is deliberately no endpoint anywhere that accepts a password or a 2FA
code (FR-04, NFR-Sec-01, AR-01) — credentials are typed on Twitch's pages only.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db import utcnow
from ..errors import ApiError
from ..models import Account, Channel, ModeratorInvite, User
from ..pipelines import start_channel_pipeline, stop_channel_pipeline
from ..rbac import (
    ROLE_MODERATOR,
    ROLE_OWNER,
    AuthContext,
    load_session,
    require_channel_owner,
    set_session_cookie,
)
from ..rules.service import seed_builtin_rules
from ..twitch.oauth import ACTION_SCOPES, READ_SCOPES, TwitchAuthError
from .deps import get_db

router = APIRouter()


@router.get("/auth/twitch/login")
async def twitch_login(request: Request, action_scopes: int = 0) -> Response:
    """IR-01: redirect to Twitch authorize URL; state lives in the server session (FR-06)."""
    state = secrets.token_urlsafe(24)
    scopes = list(READ_SCOPES) + (list(ACTION_SCOPES) if action_scopes else [])
    store = request.app.state.sessions
    existing = await load_session(request)
    if existing is not None:
        existing.data["oauth_state"] = state
        await store.save(existing.sid, existing.data)
        cookie_value = None
    else:
        cookie_value = await store.create({"oauth_state": state})
    response = RedirectResponse(
        request.app.state.oauth.authorize_url(state, scopes), status_code=302
    )
    if cookie_value:
        set_session_cookie(request, response, cookie_value)
    return response


@router.get("/auth/twitch/callback")
async def twitch_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """IR-02: state check, code exchange, channel/user upsert, session, redirect."""
    if error:
        # UC-01 A2: the user declined authorization on Twitch.
        raise ApiError(400, "access_denied", "Twitch authorization was declined")
    session = await load_session(request)
    expected_state = (session.data.get("oauth_state") if session else None) or None
    if not state or not expected_state or not secrets.compare_digest(state, expected_state):
        # FR-06 / AC-02: CSRF protection — no channel is created.
        raise ApiError(400, "state_mismatch", "OAuth state mismatch")
    if not code:
        raise ApiError(400, "missing_code", "Missing authorization code")

    # Local account (личный кабинет) linking the Twitch account: the account
    # must be email-verified before it may connect Twitch. Checked before the
    # code exchange — simplest correct point, and it does not burn the
    # one-time authorization code on a request that must fail anyway.
    account_id_raw = session.data.get("account_id") if session else None
    if account_id_raw is not None:
        linked_account = (
            await db.execute(select(Account).where(Account.id == int(account_id_raw)))
        ).scalar_one_or_none()
        if linked_account is None or not linked_account.email_verified:
            raise ApiError(
                403, "email_not_verified", "Verify your account email before linking Twitch"
            )

    app_state = request.app.state
    try:
        tokens = await app_state.oauth.exchange_code(code)
        identity = await app_state.oauth.validate(tokens.access_token)
    except TwitchAuthError as exc:
        raise ApiError(400, exc.code, str(exc)) from exc
    scopes = identity.scopes or tokens.scopes
    expires_at = datetime.fromtimestamp(utcnow().timestamp() + tokens.expires_in, tz=UTC)
    enc = app_state.cipher

    channel = (
        await db.execute(select(Channel).where(Channel.twitch_user_id == identity.user_id))
    ).scalar_one_or_none()
    role = ROLE_OWNER
    if channel is not None:
        # Owner re-login / scope upgrade.
        channel.encrypted_access_token = enc.encrypt(tokens.access_token)
        channel.encrypted_refresh_token = enc.encrypt(tokens.refresh_token or "")
        channel.token_expires_at = expires_at
        channel.scopes = list(scopes)
        channel.display_name = identity.login or channel.display_name
        channel.needs_reauth = False
    else:
        invite = (
            await db.execute(
                select(ModeratorInvite).where(ModeratorInvite.login == identity.login)
            )
        ).scalar_one_or_none()
        if invite is not None:
            role = ROLE_MODERATOR
            channel = (
                await db.execute(select(Channel).where(Channel.id == invite.channel_id))
            ).scalar_one()
        else:
            # UC-01: first connect — create the channel.
            channel = Channel(
                twitch_user_id=identity.user_id,
                display_name=identity.login,
                encrypted_access_token=enc.encrypt(tokens.access_token),
                encrypted_refresh_token=enc.encrypt(tokens.refresh_token or ""),
                token_expires_at=expires_at,
                scopes=list(scopes),
                eventsub_status="inactive",
            )
            db.add(channel)
            await db.flush()
            await seed_builtin_rules(
                db, channel.id, Path(app_state.settings.builtin_rules_dir)
            )
            await record(
                db, channel_id=channel.id, actor_type="system", action="channel.connected",
                target=f"channel:{channel.id}",
            )

    user = (
        await db.execute(select(User).where(User.twitch_user_id == identity.user_id))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            twitch_user_id=identity.user_id,
            login=identity.login,
            role=role,
            channel_id=channel.id,
        )
        db.add(user)
    user.login = identity.login
    user.encrypted_access_token = enc.encrypt(tokens.access_token)
    user.encrypted_refresh_token = enc.encrypt(tokens.refresh_token or "")
    user.token_expires_at = expires_at
    user.scopes = list(scopes)
    if account_id_raw is not None:
        user.account_id = int(account_id_raw)
    await db.flush()
    await db.commit()

    store = app_state.sessions
    data: dict[str, Any] = {
        "user_id": user.id,
        "channel_id": channel.id,
        "role": user.role,
        "twitch_user_id": identity.user_id,
        "login": identity.login,
    }
    if account_id_raw is not None:
        # Merge the local-account link into the (now Twitch-authenticated)
        # session so /auth/me and channel endpoints both work immediately.
        data["account_id"] = int(account_id_raw)
        if session is not None and "nick" in session.data:
            data["nick"] = session.data["nick"]
    if session is not None:
        await store.save(session.sid, data)
        cookie_value = None
    else:  # pragma: no cover - state check already required a session
        cookie_value = await store.create(data)

    if app_state.settings.start_workers and user.role == ROLE_OWNER:
        start_channel_pipeline(app_state, channel)

    response = RedirectResponse(
        f"{app_state.settings.frontend_origin}/dashboard", status_code=302
    )
    if cookie_value:
        set_session_cookie(request, response, cookie_value)
    return response


@router.get("/auth/me")
async def me(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Authenticated means the session carries a Twitch identity (user_id), a
    local-account identity (account_id — личный кабинет), or both. `user` and
    `channel` stay exactly as before for Twitch-linked sessions; `account`
    and `twitch_linked` are the new, additive fields."""
    session = await load_session(request)
    d = session.data if session is not None else {}
    has_user = "user_id" in d
    has_account = "account_id" in d
    if not has_user and not has_account:
        return {"authenticated": False}

    user: User | None = None
    user_out: dict[str, Any] | None = None
    channel_out: dict[str, Any] | None = None
    can_action = False
    if has_user:
        channel = (
            await db.execute(select(Channel).where(Channel.id == int(d["channel_id"])))
        ).scalar_one_or_none()
        user = (
            await db.execute(select(User).where(User.id == int(d["user_id"])))
        ).scalar_one_or_none()
        can_action = bool(
            channel is not None
            and channel.action_proxy_enabled
            and user is not None
            and user.encrypted_access_token
            and any(s in (user.scopes or []) for s in ACTION_SCOPES)
        )
        user_out = {"id": d["user_id"], "login": d.get("login"), "role": d.get("role")}
        channel_out = {
            "id": d["channel_id"],
            "display_name": channel.display_name if channel else None,
            "eventsub_status": channel.eventsub_status if channel else "inactive",
            "needs_reauth": channel.needs_reauth if channel else False,
        }

    account_id: int | None = None
    if has_account:
        account_id = int(d["account_id"])
    elif user is not None and user.account_id is not None:
        account_id = user.account_id
    account_out: dict[str, Any] | None = None
    if account_id is not None:
        account = (
            await db.execute(select(Account).where(Account.id == account_id))
        ).scalar_one_or_none()
        if account is not None:
            account_out = {
                "email": account.email,
                "nick": account.nick,
                "email_verified": account.email_verified,
            }

    return {
        "authenticated": True,
        "user": user_out,
        "channel": channel_out,
        "can_action": can_action,
        "account": account_out,
        "twitch_linked": has_user,
    }


@router.post("/auth/logout", status_code=204)
async def logout(request: Request) -> Response:
    """IR-03."""
    settings = request.app.state.settings
    cookie = request.cookies.get(settings.session_cookie_name)
    await request.app.state.sessions.delete(cookie)
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


@router.post("/channels/{channel_id}/disconnect", status_code=204)
async def disconnect_channel(
    channel_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_channel_owner),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """IR-04 / FR-09 / AC-04: revoke on Twitch, wipe tokens, stop EventSub."""
    app_state = request.app.state
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if channel is None:
        raise ApiError(404, "channel_not_found", "Channel not found")
    for blob in (channel.encrypted_access_token, channel.encrypted_refresh_token):
        if blob:
            try:
                await app_state.oauth.revoke(app_state.cipher.decrypt(blob))
            except Exception:  # noqa: BLE001 - revoke is best-effort, wipe regardless
                pass
    channel.encrypted_access_token = b""
    channel.encrypted_refresh_token = b""
    channel.token_expires_at = utcnow()
    channel.eventsub_status = "inactive"
    channel.needs_reauth = True
    await record(
        db, channel_id=channel_id, actor_type="user", actor_id=ctx.user_id,
        action="channel.disconnected", target=f"channel:{channel_id}",
    )
    await db.commit()
    await stop_channel_pipeline(app_state, channel_id)
    await app_state.hub.broadcast(
        channel_id, "channel.status", {"eventsub_status": "inactive", "needs_reauth": True}
    )
    return Response(status_code=204)


@router.post("/channels/{channel_id}/eventsub/restart", status_code=202)
async def restart_eventsub(
    channel_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_channel_owner),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """UC-01 A3: retry the EventSub subscription."""
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if channel is None or not channel.encrypted_access_token:
        raise ApiError(409, "channel_disconnected", "Reconnect the channel via Twitch first")
    await stop_channel_pipeline(request.app.state, channel_id)
    if request.app.state.settings.start_workers:
        start_channel_pipeline(request.app.state, channel)
    await record(
        db, channel_id=channel_id, actor_type="user", actor_id=ctx.user_id,
        action="eventsub.restart_requested",
    )
    await db.commit()
    return {"status": "restarting"}
