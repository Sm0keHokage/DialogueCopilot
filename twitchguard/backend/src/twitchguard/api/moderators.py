"""Moderator management, owner-only (§3.1 "Управлять модераторами", FR-01/FR-02)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..errors import ApiError
from ..models import ModeratorInvite, User
from ..rbac import ROLE_MODERATOR, AuthContext, require_channel_owner
from .deps import get_db

router = APIRouter(prefix="/channels/{channel_id}/moderators")


class InviteBody(BaseModel):
    login: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")


@router.get("")
async def list_moderators(
    channel_id: int,
    ctx: AuthContext = Depends(require_channel_owner),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    invites = list(
        (
            await db.execute(
                select(ModeratorInvite).where(ModeratorInvite.channel_id == channel_id)
            )
        ).scalars()
    )
    users = list(
        (
            await db.execute(
                select(User).where(User.channel_id == channel_id, User.role == ROLE_MODERATOR)
            )
        ).scalars()
    )
    registered = {u.login for u in users}
    return {
        "moderators": [
            {"login": i.login, "registered": i.login in registered} for i in invites
        ]
    }


@router.post("", status_code=201)
async def invite_moderator(
    channel_id: int,
    body: InviteBody,
    ctx: AuthContext = Depends(require_channel_owner),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    login = body.login.lower()
    exists = (
        await db.execute(
            select(ModeratorInvite).where(
                ModeratorInvite.channel_id == channel_id, ModeratorInvite.login == login
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise ApiError(409, "already_invited", f"'{login}' is already a moderator")
    db.add(ModeratorInvite(channel_id=channel_id, login=login))
    await record(
        db, channel_id=channel_id, actor_type="user", actor_id=ctx.user_id,
        action="moderator.invited", target=f"login:{login}",
    )
    await db.commit()
    return {"login": login, "registered": False}


@router.delete("/{login}", status_code=204)
async def remove_moderator(
    channel_id: int,
    login: str,
    ctx: AuthContext = Depends(require_channel_owner),
    db: AsyncSession = Depends(get_db),
) -> None:
    login = login.lower()
    await db.execute(
        delete(ModeratorInvite).where(
            ModeratorInvite.channel_id == channel_id, ModeratorInvite.login == login
        )
    )
    await db.execute(
        delete(User).where(
            User.channel_id == channel_id, User.role == ROLE_MODERATOR, User.login == login
        )
    )
    await record(
        db, channel_id=channel_id, actor_type="user", actor_id=ctx.user_id,
        action="moderator.removed", target=f"login:{login}",
    )
    await db.commit()
