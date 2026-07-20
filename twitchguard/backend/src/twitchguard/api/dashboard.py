"""Dashboard API (IR-16, FR-49): statuses, counters, latency, cost, precision."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..errors import ApiError
from ..flags import rule_precision
from ..ingest import recent_key
from ..models import Channel, Usage
from ..moderation.engine import backlog, latency_key
from ..pipelines import classifier_task_prefix
from ..rbac import AuthContext, require_channel_member
from .deps import get_db

router = APIRouter()


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    idx = min(int(len(sorted_values) * pct), len(sorted_values) - 1)
    return sorted_values[idx]


@router.get("/channels/{channel_id}/dashboard")
async def dashboard(
    channel_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_channel_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    app_state = request.app.state
    channel = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if channel is None:
        raise ApiError(404, "channel_not_found", "Channel not found")

    today = (
        await db.execute(
            select(Usage).where(Usage.channel_id == channel_id, Usage.day == utcnow().date())
        )
    ).scalar_one_or_none()
    totals = (
        await db.execute(
            select(
                func.coalesce(func.sum(Usage.messages_processed), 0),
                func.coalesce(func.sum(Usage.flags_created), 0),
                func.coalesce(func.sum(Usage.classification_failed), 0),
                func.coalesce(func.sum(Usage.tokens), 0),
                func.coalesce(func.sum(Usage.requests), 0),
            ).where(Usage.channel_id == channel_id)
        )
    ).one()

    raw_latencies = await app_state.redis.lrange(latency_key(channel_id), 0, -1)
    latencies = sorted(float(v) for v in raw_latencies)
    recent_raw = await app_state.redis.lrange(recent_key(channel_id), 0, 19)
    recent = [json.loads(r) for r in recent_raw]
    cfg = app_state.config.classifier
    total_tokens = int(totals[3])

    backend_cfg = dict(channel.backend_config or {})
    active_workers = len(
        app_state.supervisor.running_names(classifier_task_prefix(channel_id))
    )
    return {
        "channel": {
            "id": channel.id,
            "display_name": channel.display_name,
            "eventsub_status": channel.eventsub_status,
            "needs_reauth": channel.needs_reauth,
            "action_proxy_enabled": channel.action_proxy_enabled,
        },
        "workers": {"configured": channel.classifier_workers, "active": active_workers},
        "backend": {
            "type": backend_cfg.get("type"),
            "vendor": backend_cfg.get("vendor"),
            "cli_tool": backend_cfg.get("cli_tool"),
            "model": backend_cfg.get("model"),
            "configured": bool(backend_cfg.get("type")),
        },
        "today": {
            "messages_processed": today.messages_processed if today else 0,
            "flags_created": today.flags_created if today else 0,
            "classification_failed": today.classification_failed if today else 0,
            "tokens": today.tokens if today else 0,
            "requests": today.requests if today else 0,
        },
        "total": {
            "messages_processed": int(totals[0]),
            "flags_created": int(totals[1]),
            "classification_failed": int(totals[2]),
            "tokens": total_tokens,
            "requests": int(totals[4]),
            "cost_usd": round(total_tokens / 1_000_000 * cfg.cost_per_mtok_usd, 4),
        },
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "samples": len(latencies),
        },
        "backlog": await backlog(app_state.redis, channel_id),
        "precision": await rule_precision(db, channel_id),
        "recent_messages": recent,
    }
