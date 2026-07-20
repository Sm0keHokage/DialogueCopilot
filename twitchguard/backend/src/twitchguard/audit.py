"""Audit trail writer (DR-06, FR-38, FR-43, FR-50, FR-53)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog


async def record(
    db: AsyncSession,
    *,
    channel_id: int | None,
    actor_type: str,
    action: str,
    actor_id: int | None = None,
    target: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            channel_id=channel_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target=target,
            payload=payload or {},
        )
    )
