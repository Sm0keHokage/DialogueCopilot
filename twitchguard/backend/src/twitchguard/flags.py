"""Flags: creation, state machine (§8), queries, precision metrics (FR-32..FR-39, FR-51..FR-53)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit import record
from .db import utcnow
from .errors import ApiError
from .models import Flag
from .rbac import AuthContext
from .ws import Hub

FLAG_STATUSES = ("new", "reviewed", "dismissed", "actioned")
# FR-51: the only allowed transitions; FR-52: dismissed/actioned are terminal.
ALLOWED_TRANSITIONS: set[tuple[str, str]] = {
    ("new", "reviewed"),
    ("new", "dismissed"),
    ("new", "actioned"),
    ("reviewed", "actioned"),
    ("reviewed", "dismissed"),
}


def flag_out(flag: Flag) -> dict[str, Any]:
    return {
        "id": flag.id,
        "channel_id": flag.channel_id,
        "twitch_message_id": flag.twitch_message_id,
        "author_login": flag.author_login,
        "author_id": flag.author_id,
        "message_text": flag.message_text,
        "rule_name": flag.rule_name,
        "rule_version": flag.rule_version,
        "severity": flag.severity,
        "confidence": flag.confidence,
        "reason": flag.reason,
        "action_hint": flag.action_hint,
        "status": flag.status,
        "reviewed_by": flag.reviewed_by,
        "reviewed_at": flag.reviewed_at,
        "created_at": flag.created_at,
    }


async def create_flag(
    db: AsyncSession,
    hub: Hub,
    *,
    channel_id: int,
    twitch_message_id: str,
    author_login: str,
    author_id: str,
    message_text: str,
    rule_name: str,
    rule_version: int,
    severity: str,
    confidence: float,
    reason: str,
    action_hint: str | None,
) -> Flag:
    """FR-32: create a `new` flag; FR-33: push it to open admin sessions."""
    flag = Flag(
        channel_id=channel_id,
        twitch_message_id=twitch_message_id,
        author_login=author_login,
        author_id=author_id,
        message_text=message_text,
        rule_name=rule_name,
        rule_version=rule_version,
        severity=severity,
        confidence=confidence,
        reason=reason or "(модель не указала причину)",
        action_hint=action_hint,
        status="new",
    )
    db.add(flag)
    await db.flush()
    await record(
        db,
        channel_id=channel_id,
        actor_type="system",
        action="flag.created",
        target=f"flag:{flag.id}",
        payload={"rule": rule_name, "rule_version": rule_version, "confidence": confidence},
    )
    await hub.broadcast(channel_id, "flag.created", flag_out(flag))
    return flag


async def get_flag(db: AsyncSession, channel_id: int, flag_id: int) -> Flag:
    flag = (
        await db.execute(
            select(Flag).where(Flag.id == flag_id, Flag.channel_id == channel_id)
        )
    ).scalar_one_or_none()
    if flag is None:
        raise ApiError(404, "flag_not_found", f"Flag {flag_id} not found")
    return flag


async def change_status(
    db: AsyncSession, hub: Hub, flag: Flag, new_status: str, actor: AuthContext
) -> Flag:
    """FR-35/FR-51/FR-52: validated transition; FR-38/FR-53: audited; 409 otherwise."""
    if new_status not in FLAG_STATUSES:
        raise ApiError(422, "invalid_status", f"Unknown status '{new_status}'", field="status")
    if (flag.status, new_status) not in ALLOWED_TRANSITIONS:
        raise ApiError(
            409,
            "invalid_transition",
            f"Transition {flag.status} -> {new_status} is not allowed",
        )
    old_status = flag.status
    flag.status = new_status
    flag.reviewed_by = actor.user_id
    flag.reviewed_at = utcnow()
    await record(
        db,
        channel_id=flag.channel_id,
        actor_type="user",
        actor_id=actor.user_id,
        action="flag.status_changed",
        target=f"flag:{flag.id}",
        payload={"from": old_status, "to": new_status},
    )
    await db.flush()
    await hub.broadcast(flag.channel_id, "flag.updated", flag_out(flag))
    return flag


async def query_flags(
    db: AsyncSession,
    channel_id: int,
    *,
    status: str | None = None,
    rule: str | None = None,
    severity: str | None = None,
    author: str | None = None,
    limit: int = 50,
    cursor: int | None = None,
) -> tuple[list[Flag], int | None]:
    """IR-10: cursor pagination + FR-37 filters."""
    stmt = select(Flag).where(Flag.channel_id == channel_id)
    if status:
        stmt = stmt.where(Flag.status == status)
    if rule:
        stmt = stmt.where(Flag.rule_name == rule)
    if severity:
        stmt = stmt.where(Flag.severity == severity)
    if author:
        stmt = stmt.where(Flag.author_login == author)
    if cursor:
        stmt = stmt.where(Flag.id < cursor)
    stmt = stmt.order_by(Flag.id.desc()).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars())
    next_cursor = rows[-1].id if len(rows) > limit else None
    return rows[:limit], next_cursor


async def snapshot_flags(db: AsyncSession, channel_id: int, limit: int = 100) -> list[Flag]:
    """FR-39: queue state restored from the DB on (re)connect."""
    rows = await db.execute(
        select(Flag)
        .where(Flag.channel_id == channel_id, Flag.status.in_(("new", "reviewed")))
        .order_by(Flag.id.desc())
        .limit(limit)
    )
    return list(rows.scalars())


async def rule_precision(db: AsyncSession, channel_id: int) -> list[dict[str, Any]]:
    """FR-36: dismissed flags = false positives; precision per rule for the dashboard."""
    rows = await db.execute(
        select(Flag.rule_name, Flag.status, func.count())
        .where(Flag.channel_id == channel_id)
        .group_by(Flag.rule_name, Flag.status)
    )
    per_rule: dict[str, dict[str, int]] = {}
    for rule_name, status, count in rows:
        per_rule.setdefault(rule_name, {})[status] = count
    out = []
    for rule_name, counts in sorted(per_rule.items()):
        dismissed = counts.get("dismissed", 0)
        confirmed = counts.get("reviewed", 0) + counts.get("actioned", 0)
        total_reviewed = dismissed + confirmed
        out.append(
            {
                "rule_name": rule_name,
                "flags_total": sum(counts.values()),
                "dismissed": dismissed,
                "confirmed": confirmed,
                "precision": round(confirmed / total_reviewed, 3) if total_reviewed else None,
            }
        )
    return out
