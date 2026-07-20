"""Settings API: backend switch and Action Proxy (IR-13..IR-15, UC-06, FR-44..FR-48)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..errors import ApiError
from ..models import Channel, User
from ..moderation.backends import BackendContext, build_backend
from ..moderation.backends.base import BackendUnavailable
from ..rbac import AuthContext, require_channel_owner
from ..twitch.oauth import ACTION_SCOPES
from .deps import get_db

router = APIRouter(prefix="/channels/{channel_id}/settings")


class BackendBody(BaseModel):
    type: Literal["api", "cli"]
    vendor: Literal["anthropic", "openai", "deepseek"] | None = None
    api_key: str | None = None
    model: str | None = None
    # FR-48 / AC-12: no "deepseek" here — DeepSeek is API-only by construction.
    cli_tool: Literal["claude", "gemini", "codex"] | None = None


class ActionProxyBody(BaseModel):
    enabled: bool


def _redacted_backend(config: dict[str, Any]) -> dict[str, Any]:
    """IR-13 / AR-05: the key never leaves the server."""
    return {
        "type": config.get("type"),
        "vendor": config.get("vendor"),
        "cli_tool": config.get("cli_tool"),
        "model": config.get("model"),
        "has_api_key": bool(config.get("encrypted_api_key")),
    }


async def _get_channel(db: AsyncSession, channel_id: int) -> Channel:
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if channel is None:
        raise ApiError(404, "channel_not_found", "Channel not found")
    return channel


@router.get("")
async def get_settings(
    channel_id: int,
    ctx: AuthContext = Depends(require_channel_owner),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    channel = await _get_channel(db, channel_id)
    return {
        "backend": _redacted_backend(dict(channel.backend_config or {})),
        "action_proxy_enabled": channel.action_proxy_enabled,
        "required_action_scopes": list(ACTION_SCOPES),
        "granted_scopes": list(channel.scopes or []),
    }


@router.put("/backend")
async def put_backend(
    channel_id: int,
    body: BackendBody,
    request: Request,
    ctx: AuthContext = Depends(require_channel_owner),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """IR-14: validate first; on failure the previous backend stays active (FR-47)."""
    channel = await _get_channel(db, channel_id)
    app_state = request.app.state
    old = dict(channel.backend_config or {})

    candidate: dict[str, Any] = {"type": body.type}
    if body.type == "api":
        if not body.vendor:
            raise ApiError(422, "missing_vendor", "vendor is required for an API backend",
                           field="vendor")
        candidate["vendor"] = body.vendor
        if body.model:
            candidate["model"] = body.model
        if body.api_key:
            candidate["encrypted_api_key"] = app_state.cipher.encrypt_str(body.api_key)
        elif old.get("vendor") == body.vendor and old.get("encrypted_api_key"):
            candidate["encrypted_api_key"] = old["encrypted_api_key"]  # keep the stored key
        else:
            raise ApiError(400, "missing_api_key", "API key is required", field="api_key")
    else:
        if not body.cli_tool:
            raise ApiError(422, "missing_cli_tool", "cli_tool is required for a CLI backend",
                           field="cli_tool")
        candidate["cli_tool"] = body.cli_tool

    try:
        backend = build_backend(
            BackendContext(
                cfg=app_state.config.classifier, http=app_state.http, cipher=app_state.cipher
            ),
            candidate,
        )
        await backend.validate()
    except BackendUnavailable as exc:
        # FR-47/AC-11: clear error, previous backend remains active.
        raise ApiError(400, "backend_unavailable", exc.message) from exc

    channel.backend_config = candidate
    await record(
        db, channel_id=channel_id, actor_type="user", actor_id=ctx.user_id,
        action="settings.backend_changed",
        payload={"type": body.type, "vendor": body.vendor, "cli_tool": body.cli_tool},
    )
    await db.commit()
    return {"backend": _redacted_backend(candidate)}


@router.put("/action-proxy")
async def put_action_proxy(
    channel_id: int,
    body: ActionProxyBody,
    request: Request,
    ctx: AuthContext = Depends(require_channel_owner),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """IR-15: toggle; when scopes are missing, point at the re-auth URL (FR-55)."""
    channel = await _get_channel(db, channel_id)
    channel.action_proxy_enabled = body.enabled
    await record(
        db, channel_id=channel_id, actor_type="user", actor_id=ctx.user_id,
        action="settings.action_proxy", payload={"enabled": body.enabled},
    )
    await db.commit()
    user = (
        await db.execute(select(User).where(User.id == ctx.user_id))
    ).scalar_one_or_none()
    granted = set(user.scopes or []) if user else set()
    reauth_required = body.enabled and not all(s in granted for s in ACTION_SCOPES)
    return {
        "enabled": body.enabled,
        "reauth_required": reauth_required,
        "reauth_url": "/auth/twitch/login?action_scopes=1" if reauth_required else None,
    }
