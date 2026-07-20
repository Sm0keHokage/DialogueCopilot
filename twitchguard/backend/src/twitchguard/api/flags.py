"""Flags API (IR-10..IR-12, UC-04, UC-05)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..actions import apply_action
from ..errors import ApiError
from ..flags import change_status, flag_out, get_flag, query_flags
from ..models import Channel
from ..rbac import AuthContext, require_channel_member
from .deps import get_db

router = APIRouter(prefix="/channels/{channel_id}/flags")


class FlagStatusBody(BaseModel):
    # IR-11: moderators may set reviewed/dismissed; `actioned` only via /action (UC-05).
    status: Literal["reviewed", "dismissed"]


class ActionBody(BaseModel):
    type: Literal["delete", "timeout", "ban"]
    duration_s: int | None = None


@router.get("")
async def list_flags(
    channel_id: int,
    ctx: AuthContext = Depends(require_channel_member),
    db: AsyncSession = Depends(get_db),
    status: str | None = None,
    rule: str | None = None,
    severity: str | None = None,
    author: str | None = None,
    limit: int = 50,
    cursor: int | None = None,
) -> dict[str, Any]:
    """IR-10: cursor-paginated, filterable queue (FR-37, FR-39)."""
    items, next_cursor = await query_flags(
        db,
        channel_id,
        status=status,
        rule=rule,
        severity=severity,
        author=author,
        limit=min(max(limit, 1), 200),
        cursor=cursor,
    )
    return {"items": [flag_out(f) for f in items], "next_cursor": next_cursor}


@router.patch("/{flag_id}")
async def patch_flag(
    channel_id: int,
    flag_id: int,
    body: FlagStatusBody,
    request: Request,
    ctx: AuthContext = Depends(require_channel_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """IR-11: state machine transition or 409 (FR-51/FR-52, AC-10)."""
    flag = await get_flag(db, channel_id, flag_id)
    flag = await change_status(db, request.app.state.hub, flag, body.status, ctx)
    await db.commit()
    return flag_out(flag)


@router.post("/{flag_id}/action")
async def action_flag(
    channel_id: int,
    flag_id: int,
    body: ActionBody,
    request: Request,
    ctx: AuthContext = Depends(require_channel_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """IR-12 / UC-05: Helix under the acting human moderator's token."""
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if channel is None:
        raise ApiError(404, "channel_not_found", "Channel not found")
    app_state = request.app.state
    flag = await apply_action(
        db,
        app_state.hub,
        app_state.helix,
        app_state.oauth,
        app_state.cipher,
        channel=channel,
        flag_id=flag_id,
        actor=ctx,
        action_type=body.type,
        duration_s=body.duration_s,
    )
    await db.commit()
    return flag_out(flag)
