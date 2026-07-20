"""Action Proxy: a human moderator applies an action via Helix (§10, FR-40..FR-43, FR-54..FR-56).

Every call runs under the acting moderator's own user token — never a bot
token (FR-41, FR-54, AR-04). Nothing here is ever triggered automatically.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit import record
from .crypto import TokenCipher
from .db import as_utc, utcnow
from .errors import ApiError
from .flags import change_status, get_flag
from .models import Channel, Flag, User
from .rbac import AuthContext
from .twitch.helix import HelixClient, HelixError
from .twitch.oauth import TwitchAuthError, TwitchOAuth
from .ws import Hub

SCOPE_BY_ACTION = {
    "delete": "moderator:manage:chat_messages",
    "timeout": "moderator:manage:banned_users",
    "ban": "moderator:manage:banned_users",
}
MAX_TIMEOUT_S = 1_209_600  # Helix limit: 2 weeks


async def _acting_token(
    db: AsyncSession, oauth: TwitchOAuth, cipher: TokenCipher, user: User
) -> str:
    """Decrypt the acting user's token, refreshing it if expired (FR-08)."""
    if not user.encrypted_access_token:
        raise ApiError(403, "token_missing", "Your Twitch token is missing — re-login required")
    expires = as_utc(user.token_expires_at)
    if expires is not None and expires <= utcnow() and user.encrypted_refresh_token:
        try:
            tokens = await oauth.refresh(cipher.decrypt(user.encrypted_refresh_token))
        except TwitchAuthError as exc:
            raise ApiError(403, "token_invalid", "Your Twitch token expired — re-login") from exc
        user.encrypted_access_token = cipher.encrypt(tokens.access_token)
        if tokens.refresh_token:
            user.encrypted_refresh_token = cipher.encrypt(tokens.refresh_token)
        user.token_expires_at = datetime.fromtimestamp(
            utcnow().timestamp() + tokens.expires_in, tz=UTC
        )
        await db.flush()
    return cipher.decrypt(user.encrypted_access_token)


async def apply_action(
    db: AsyncSession,
    hub: Hub,
    helix: HelixClient,
    oauth: TwitchOAuth,
    cipher: TokenCipher,
    *,
    channel: Channel,
    flag_id: int,
    actor: AuthContext,
    action_type: str,
    duration_s: int | None,
) -> Flag:
    if action_type not in SCOPE_BY_ACTION:
        raise ApiError(422, "invalid_action", f"Unknown action '{action_type}'", field="type")
    # FR-40(a): the owner must have enabled Action Proxy.
    if not channel.action_proxy_enabled:
        raise ApiError(403, "action_proxy_disabled", "Action Proxy is disabled for this channel")
    user = (
        await db.execute(select(User).where(User.id == actor.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise ApiError(403, "forbidden", "Unknown user")
    # FR-40(b)/FR-55: the acting user's token must carry the required scope.
    required_scope = SCOPE_BY_ACTION[action_type]
    if required_scope not in (user.scopes or []):
        raise ApiError(
            403,
            "missing_scope",
            f"Scope '{required_scope}' was not granted — reconnect via Twitch with "
            "action scopes enabled",
        )
    flag = await get_flag(db, channel.id, flag_id)
    # FR-56: idempotency — acting on a terminal flag is a 409 (state machine).
    if (flag.status, "actioned") not in {("new", "actioned"), ("reviewed", "actioned")}:
        raise ApiError(409, "invalid_transition", f"Flag is already '{flag.status}'")
    if action_type == "timeout":
        if not duration_s or duration_s < 1 or duration_s > MAX_TIMEOUT_S:
            raise ApiError(
                422, "invalid_duration", "duration_s must be 1..1209600 for a timeout",
                field="duration_s",
            )
    token = await _acting_token(db, oauth, cipher, user)

    try:
        # FR-41/IR-18: Helix under the human moderator's token.
        if action_type == "delete":
            await helix.delete_chat_message(
                token, channel.twitch_user_id, user.twitch_user_id, flag.twitch_message_id
            )
        else:
            await helix.ban_user(
                token,
                channel.twitch_user_id,
                user.twitch_user_id,
                flag.author_id,
                duration_s=duration_s if action_type == "timeout" else None,
                reason=f"TwitchGuard: {flag.rule_name} (moderator decision)",
            )
    except HelixError as exc:
        # FR-42: surface the error, keep the flag status, audit the failure.
        # Committed here because the raised ApiError rolls the request back.
        await record(
            db,
            channel_id=channel.id,
            actor_type="user",
            actor_id=actor.user_id,
            action="action.failed",
            target=f"flag:{flag.id}",
            payload={"type": action_type, "helix_status": exc.status_code, "error": exc.message},
        )
        await db.commit()
        raise ApiError(
            502, "helix_error", f"Twitch rejected the action: {exc.message}"
        ) from exc

    payload: dict[str, Any] = {
        "type": action_type,
        "target_user_id": flag.author_id,
        "target_login": flag.author_login,
        "message_id": flag.twitch_message_id,
    }
    if action_type == "timeout":
        payload["duration_s"] = duration_s
    # FR-43: full audit of who did what to whom.
    await record(
        db,
        channel_id=channel.id,
        actor_type="user",
        actor_id=actor.user_id,
        action="action.applied",
        target=f"flag:{flag.id}",
        payload=payload,
    )
    return await change_status(db, hub, flag, "actioned", actor)
