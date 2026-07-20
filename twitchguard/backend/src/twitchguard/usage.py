"""Daily usage counters (DR-07, FR-27, FR-49)."""
from __future__ import annotations

from sqlalchemy import select
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
    day = utcnow().date()
    row = (
        await db.execute(
            select(Usage).where(Usage.channel_id == channel_id, Usage.day == day)
        )
    ).scalar_one_or_none()
    if row is None:
        row = Usage(
            channel_id=channel_id,
            day=day,
            messages_processed=0,
            flags_created=0,
            classification_failed=0,
            tokens=0,
            requests=0,
        )
        db.add(row)
    row.messages_processed += messages
    row.flags_created += flags
    row.classification_failed += failed
    row.tokens += tokens
    row.requests += requests
