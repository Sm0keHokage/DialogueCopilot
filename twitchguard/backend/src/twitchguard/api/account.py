"""Local account system (личный кабинет): email+nick+password, email
verification, brute-force-protected login, "logout everywhere".

Twitch credentials are never accepted here or anywhere else in the system
(NFR-Sec-01, AR-01) — a local account's password is a completely separate
secret, hashed with stdlib scrypt (see accounts.py) and never shared with the
Twitch OAuth flow (see the email_not_verified gate in api/auth.py).
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..accounts import (
    DUMMY_PASSWORD_HASH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    hash_token,
    make_verify_token,
    verify_password,
)
from ..audit import record
from ..db import as_utc, utcnow
from ..errors import ApiError
from ..models import Account, User
from ..rbac import AccountContext, require_account, set_session_cookie
from .deps import get_db

router = APIRouter(prefix="/account")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NICK_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")

_LOGIN_FAIL_LIMIT = 5
_LOGIN_FAIL_WINDOW_S = 900
_RESEND_WINDOW_S = 60
_VERIFY_TTL_H = 24

_GENERIC_REGISTER_MSG = {"message": "Проверьте почту — мы отправили ссылку подтверждения"}
_GENERIC_RESEND_MSG = {"message": "Если такой адрес зарегистрирован, мы отправили новую ссылку"}


class RegisterBody(BaseModel):
    email: str
    nick: str
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v

    @field_validator("nick")
    @classmethod
    def _normalize_nick(cls, v: str) -> str:
        v = v.strip().lower()
        if not _NICK_RE.match(v):
            raise ValueError("nick must be 3-32 characters: letters, digits, underscore")
        return v


class VerifyBody(BaseModel):
    token: str


class ResendBody(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class LoginBody(BaseModel):
    login: str
    password: str


class PasswordChangeBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


def _verify_link(request: Request, token: str) -> str:
    settings = request.app.state.settings
    base = (settings.public_base_url or settings.frontend_origin).rstrip("/")
    return f"{base}/verify?token={token}"


async def _send_verify_mail(request: Request, email: str, token: str) -> None:
    link = _verify_link(request, token)
    body = (
        "Здравствуйте!\n\n"
        "Подтвердите почту, перейдя по ссылке:\n"
        f"{link}\n\n"
        f"Ссылка действует {_VERIFY_TTL_H} часа. Если вы не регистрировались "
        "в TwitchGuard, просто проигнорируйте это письмо."
    )
    await request.app.state.emailer.send(email, "Подтверждение почты — TwitchGuard", body)


@router.post("/register", status_code=201)
async def register(
    body: RegisterBody, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Anti-enumeration: always 201 with the same message, whether or not the
    email/nick was already taken — no signal is leaked either way."""
    exists = (
        await db.execute(
            select(Account).where((Account.email == body.email) | (Account.nick == body.nick))
        )
    ).scalar_one_or_none()
    if exists is not None:
        return _GENERIC_REGISTER_MSG

    token, token_hash = make_verify_token()
    account = Account(
        email=body.email,
        nick=body.nick,
        password_hash=hash_password(body.password),
        email_verified=False,
        verify_token_hash=token_hash,
        verify_expires_at=utcnow() + timedelta(hours=_VERIFY_TTL_H),
    )
    db.add(account)
    try:
        await db.flush()
    except IntegrityError:
        # A concurrent registration won the race for this email/nick.
        await db.rollback()
        return _GENERIC_REGISTER_MSG
    await record(
        db, channel_id=None, actor_type="user", actor_id=account.id,
        action="account.registered", target=f"account:{account.id}",
    )
    await db.commit()
    await _send_verify_mail(request, body.email, token)
    return _GENERIC_REGISTER_MSG


@router.post("/verify")
async def verify(body: VerifyBody, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    token_hash = hash_token(body.token)
    account = (
        await db.execute(select(Account).where(Account.verify_token_hash == token_hash))
    ).scalar_one_or_none()
    expires = as_utc(account.verify_expires_at) if account is not None else None
    if account is None or expires is None or expires < utcnow():
        raise ApiError(400, "invalid_token", "Verification link is invalid or has expired")
    account.email_verified = True
    account.verify_token_hash = None
    account.verify_expires_at = None
    await db.commit()
    return {"verified": True}


@router.post("/resend")
async def resend(
    body: ResendBody, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """One mail per email per 60s (redis SET NX EX); anti-enumeration generic
    response regardless of whether the address is registered/verified."""
    redis = request.app.state.redis
    allowed = await redis.set(f"tg:resend:{body.email}", "1", ex=_RESEND_WINDOW_S, nx=True)
    if not allowed:
        return _GENERIC_RESEND_MSG
    account = (
        await db.execute(select(Account).where(Account.email == body.email))
    ).scalar_one_or_none()
    if account is None or account.email_verified:
        return _GENERIC_RESEND_MSG
    token, token_hash = make_verify_token()
    account.verify_token_hash = token_hash
    account.verify_expires_at = utcnow() + timedelta(hours=_VERIFY_TTL_H)
    await db.commit()
    await _send_verify_mail(request, account.email, token)
    return _GENERIC_RESEND_MSG


@router.post("/login")
async def account_login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Login by email or nick, brute-force limited (5 failures / 15 min per
    login+IP). Wrong login and wrong password give the identical response."""
    login_key = body.login.strip().lower()
    client_ip = request.client.host if request.client else "unknown"
    redis = request.app.state.redis
    fail_key = f"tg:loginfail:{login_key}:{client_ip}"
    attempts = await redis.incr(fail_key)
    if attempts == 1:
        await redis.expire(fail_key, _LOGIN_FAIL_WINDOW_S)
    if attempts > _LOGIN_FAIL_LIMIT:
        raise ApiError(429, "too_many_attempts", "Too many failed attempts, try again later")

    account = (
        await db.execute(
            select(Account).where((Account.email == login_key) | (Account.nick == login_key))
        )
    ).scalar_one_or_none()
    if account is None:
        verify_password(body.password, DUMMY_PASSWORD_HASH)  # constant-shaped timing
        raise ApiError(401, "invalid_credentials", "Invalid login or password")
    if not verify_password(body.password, account.password_hash):
        raise ApiError(401, "invalid_credentials", "Invalid login or password")

    await redis.delete(fail_key)

    data: dict[str, Any] = {"account_id": account.id, "nick": account.nick}
    user = (
        await db.execute(select(User).where(User.account_id == account.id))
    ).scalar_one_or_none()
    if user is not None:
        data.update(
            user_id=user.id,
            channel_id=user.channel_id,
            role=user.role,
            twitch_user_id=user.twitch_user_id,
            login=user.login,
        )

    store = request.app.state.sessions
    old_cookie = request.cookies.get(request.app.state.settings.session_cookie_name)
    if old_cookie:
        # Session fixation defense: never write the newly authenticated
        # payload into a pre-existing session id, always mint a fresh one.
        await store.delete(old_cookie)
    cookie_value = await store.create(data)
    set_session_cookie(request, response, cookie_value)
    return {"ok": True}


@router.post("/password", status_code=204)
async def change_password(
    body: PasswordChangeBody,
    request: Request,
    ctx: AccountContext = Depends(require_account),
    db: AsyncSession = Depends(get_db),
) -> Response:
    account = (
        await db.execute(select(Account).where(Account.id == ctx.account_id))
    ).scalar_one_or_none()
    if account is None or not verify_password(body.current_password, account.password_hash):
        raise ApiError(403, "wrong_password", "Current password is incorrect")
    account.password_hash = hash_password(body.new_password)
    await record(
        db, channel_id=None, actor_type="user", actor_id=account.id,
        action="account.password_changed", target=f"account:{account.id}",
    )
    await db.commit()
    await request.app.state.sessions.delete_account_sessions(
        account.id, keep_sid=ctx.session.sid
    )
    return Response(status_code=204)


@router.post("/logout-all", status_code=204)
async def logout_all(
    request: Request,
    ctx: AccountContext = Depends(require_account),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await request.app.state.sessions.delete_account_sessions(ctx.account_id)
    await record(
        db, channel_id=None, actor_type="user", actor_id=ctx.account_id,
        action="account.logout_all", target=f"account:{ctx.account_id}",
    )
    await db.commit()
    response = Response(status_code=204)
    response.delete_cookie(request.app.state.settings.session_cookie_name, path="/")
    return response
