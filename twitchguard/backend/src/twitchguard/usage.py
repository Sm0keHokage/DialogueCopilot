"""Daily usage counters (DR-07, FR-27, FR-49).

Implemented as an atomic upsert so parallel classifier agents never lose
increments to read-modify-write races.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import utcnow
from .models import Usage


async def bump(
    db: AsyncSession,
    channel_id: int,
    *,
    messages: int = 0,
    flags: int = 0,
    failed: int = 0,
    tokens: int = 0,
    requests: int = 0,
) -> None:
    deltas = {
        "messages_processed": messages,
        "flags_created": flags,
        "classification_failed": failed,
        "tokens": tokens,
        "requests": requests,
    }
    values: dict[str, Any] = {
        "channel_id": channel_id,
        "day": utcnow().date(),
        "created_at": utcnow(),
        **deltas,
    }
    dialect = db.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert(Usage).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Usage.channel_id, Usage.day],
        set_={name: getattr(Usage, name) + delta for name, delta in deltas.items()},
    )
    await db.execute(stmt)
