"""Classification worker: Redis Stream batch -> LLM -> flags (UC-03, FR-23..FR-31).

Consumer-group semantics give at-least-once processing: messages are ACKed only
after successful classification or a definitive failure (FR-27), so a backend
outage never loses messages (NFR-Rel-03, AC-13).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select

from .. import flags as flag_service
from ..audit import record
from ..ingest import stream_key
from ..lang import detect_language, rule_applies_to_language
from ..models import Channel, Rule
from ..rules.service import get_active_rules, rules_fingerprint
from ..usage import bump
from .backends import BackendContext, build_backend
from .backends.base import BackendUnavailable, ClassificationError
from .types import ChatMessage
from .verdicts import Verdict

log = logging.getLogger(__name__)

GROUP = "classifier"
CONSUMER = "worker-1"


def consumer_name(n: int) -> str:
    return f"worker-{n}"


def cache_key(channel_id: int, text: str, rules_fp: str) -> str:
    digest = hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:32]
    return f"tg:cls:{channel_id}:{rules_fp}:{digest}"


def slowdown_key(channel_id: int) -> str:
    return f"tg:slow:{channel_id}"


def latency_key(channel_id: int) -> str:
    return f"tg:lat:{channel_id}"


async def ensure_group(redis: Redis, key: str) -> None:
    try:
        await redis.xgroup_create(key, GROUP, id="0", mkstream=True)
    except Exception as exc:  # noqa: BLE001 - BUSYGROUP means it already exists
        if "BUSYGROUP" not in str(exc):
            raise


def _entry_to_message(entry_id: str, fields: dict[str, str]) -> ChatMessage:
    return ChatMessage(
        message_id=fields.get("message_id", ""),
        author_id=fields.get("author_id", ""),
        author_login=fields.get("author_login", ""),
        text=fields.get("text", ""),
        ts_ms=int(fields.get("ts_ms") or 0),
        stream_id=entry_id,
    )


async def _reclaim_stale(
    app_state: Any, channel_id: int, consumer: str, count: int
) -> list[ChatMessage]:
    """Steal pending entries whose agent died or hung (multi-agent recovery)."""
    redis: Redis = app_state.redis
    cfg = app_state.config.classifier
    try:
        result: Any = await redis.xautoclaim(
            stream_key(channel_id),
            GROUP,
            consumer,
            min_idle_time=cfg.reclaim_idle_ms,
            start_id="0-0",
            count=count,
        )
    except Exception:  # noqa: BLE001 - reclaim is best-effort, never fatal
        return []
    entries = result[1] if isinstance(result, list | tuple) and len(result) >= 2 else []
    return [_entry_to_message(eid, fields) for eid, fields in entries]


async def _read_batch(app_state: Any, channel_id: int, consumer: str) -> list[ChatMessage]:
    redis: Redis = app_state.redis
    cfg = app_state.config.classifier
    key = stream_key(channel_id)
    await ensure_group(redis, key)
    # Own pending entries first (redelivery after crash/outage), then stale
    # entries of dead sibling agents, then fresh ones. The consumer group
    # shards fresh messages between agents automatically.
    batch: list[ChatMessage] = []
    seen: set[str] = set()

    def _add(messages: list[ChatMessage]) -> None:
        for msg in messages:
            if msg.stream_id not in seen:
                seen.add(msg.stream_id)
                batch.append(msg)

    pending: Any = await redis.xreadgroup(GROUP, consumer, {key: "0"}, count=cfg.batch_size)
    for _stream, entries in pending or []:
        _add([_entry_to_message(eid, fields) for eid, fields in entries])
    if len(batch) < cfg.batch_size:
        _add(await _reclaim_stale(app_state, channel_id, consumer, cfg.batch_size - len(batch)))
    if len(batch) < cfg.batch_size:
        block_ms = cfg.batch_window_ms if cfg.batch_window_ms > 0 else None
        fresh: Any = await redis.xreadgroup(
            GROUP,
            consumer,
            {key: ">"},
            count=cfg.batch_size - len(batch),
            block=block_ms,
        )
        for _stream, entries in fresh or []:
            _add([_entry_to_message(eid, fields) for eid, fields in entries])
    return batch


def _verdicts_for_flagging(
    messages: list[ChatMessage],
    rules: list[Rule],
    verdicts: list[Verdict],
) -> list[tuple[ChatMessage, Rule, Verdict]]:
    """FR-26 (threshold) + FR-31 (language) applied to raw model verdicts."""
    by_name = {r.name: r for r in rules}
    by_id = {m.message_id: m for m in messages}
    out = []
    for v in verdicts:
        rule = by_name.get(v.rule)
        msg = by_id.get(v.message_id)
        if rule is None or msg is None or not v.is_violation:
            continue
        fm = rule.frontmatter or {}
        if v.confidence < float(fm.get("confidence_threshold", 1.0)):
            continue
        if not rule_applies_to_language(fm.get("languages"), detect_language(msg.text)):
            continue
        out.append((msg, rule, v))
    return out


async def _apply_cached(
    redis: Redis, channel_id: int, rules_fp: str, messages: list[ChatMessage]
) -> tuple[list[ChatMessage], list[Verdict]]:
    """FR-30: reuse verdicts for identical texts within the cache TTL."""
    remaining: list[ChatMessage] = []
    cached_verdicts: list[Verdict] = []
    for msg in messages:
        raw = await redis.get(cache_key(channel_id, msg.text, rules_fp))
        if raw is None:
            remaining.append(msg)
            continue
        for item in json.loads(raw):
            cached_verdicts.append(Verdict.model_validate({**item, "message_id": msg.message_id}))
    return remaining, cached_verdicts


async def _store_cache(
    redis: Redis,
    channel_id: int,
    rules_fp: str,
    messages: list[ChatMessage],
    verdicts: list[Verdict],
    ttl: int,
) -> None:
    by_msg: dict[str, list[dict[str, Any]]] = {m.message_id: [] for m in messages}
    for v in verdicts:
        if v.message_id in by_msg:
            item = v.model_dump()
            item.pop("message_id", None)
            by_msg[v.message_id].append(item)
    for msg in messages:
        await redis.set(
            cache_key(channel_id, msg.text, rules_fp),
            json.dumps(by_msg[msg.message_id]),
            ex=ttl,
        )


async def run_cycle(app_state: Any, channel_id: int, consumer: str = CONSUMER) -> int:
    """One classification cycle of one AI agent. Returns messages processed."""
    redis: Redis = app_state.redis
    cfg = app_state.config.classifier
    key = stream_key(channel_id)

    slowdown = await redis.get(slowdown_key(channel_id))
    if slowdown:
        await asyncio.sleep(min(float(slowdown), cfg.backoff_max_s))

    batch = await _read_batch(app_state, channel_id, consumer)
    if not batch:
        return 0

    async with app_state.sessionmaker() as db:
        channel = (
            await db.execute(select(Channel).where(Channel.id == channel_id))
        ).scalar_one_or_none()
        if channel is None:
            await redis.xack(key, GROUP, *[m.stream_id for m in batch])
            return len(batch)
        rules = await get_active_rules(db, channel_id)  # hot reload point (FR-20/FR-23)
        if not rules:
            await redis.xack(key, GROUP, *[m.stream_id for m in batch])
            await bump(db, channel_id, messages=len(batch))
            await db.commit()
            return len(batch)
        rules_fp = rules_fingerprint(rules)
        to_classify, verdicts = await _apply_cached(redis, channel_id, rules_fp, batch)
        tokens = requests = 0
        failed: list[ChatMessage] = []
        if to_classify:
            try:
                backend = build_backend(
                    BackendContext(cfg=cfg, http=app_state.http, cipher=app_state.cipher),
                    dict(channel.backend_config or {}),
                )
                result = await backend.classify(to_classify, rules)
            except BackendUnavailable as exc:
                # NFR-Rel-03: leave everything pending, slow down, try later (FR-28).
                prev = float(await redis.get(slowdown_key(channel_id)) or 0)
                pause = exc.retry_after_s or max(prev * 2, cfg.backoff_base_s)
                await redis.set(
                    slowdown_key(channel_id), str(min(pause, cfg.backoff_max_s)), ex=300
                )
                log.warning("channel %s classification paused: %s", channel_id, exc.code)
                return 0
            except ClassificationError as exc:
                # FR-27: definitive failure — count and audit it, drop only this
                # batch, keep the service alive. Cached verdicts still flag below.
                failed = to_classify
                tokens, requests = exc.tokens, exc.requests
                await record(
                    db,
                    channel_id=channel_id,
                    actor_type="system",
                    action="classification.failed",
                    payload={"message_ids": [m.message_id for m in to_classify]},
                )
            else:
                verdicts.extend(result.verdicts)
                tokens, requests = result.tokens, result.requests
                await _store_cache(
                    redis, channel_id, rules_fp, to_classify, result.verdicts, cfg.cache_ttl_s
                )
        if not failed:
            await redis.delete(slowdown_key(channel_id))

        flags_created = 0
        for msg, rule, verdict in _verdicts_for_flagging(batch, rules, verdicts):
            fm = rule.frontmatter or {}
            await flag_service.create_flag(
                db,
                app_state.hub,
                channel_id=channel_id,
                twitch_message_id=msg.message_id,
                author_login=msg.author_login,
                author_id=msg.author_id,
                message_text=msg.text,
                rule_name=rule.name,
                rule_version=rule.version,
                severity=str(fm.get("severity", "low")),
                confidence=verdict.confidence,
                reason=verdict.reason,
                action_hint=verdict.action_hint or fm.get("action_hint"),
            )
            flags_created += 1
        await bump(
            db,
            channel_id,
            messages=len(batch),
            flags=flags_created,
            failed=len(failed),
            tokens=tokens,
            requests=requests,
        )
        await db.commit()

    now_ms = int(time.time() * 1000)
    for msg in batch:
        if msg.ts_ms:
            await redis.lpush(latency_key(channel_id), str(now_ms - msg.ts_ms))
    await redis.ltrim(latency_key(channel_id), 0, cfg.latency_samples - 1)
    await redis.xack(key, GROUP, *[m.stream_id for m in batch])
    return len(batch)


async def classifier_loop(app_state: Any, channel_id: int, consumer: str = CONSUMER) -> None:
    """One long-running AI agent; the supervisor restarts it on crash.

    A channel may run several of these in parallel (channel.classifier_workers):
    the Redis consumer group shards messages between them, so N agents give
    ~N concurrent LLM calls for fast-moving chats.
    """
    cfg = app_state.config.classifier
    while True:
        processed = await run_cycle(app_state, channel_id, consumer)
        if processed == 0:
            await asyncio.sleep(cfg.idle_sleep_s)


async def backlog(redis: Redis, channel_id: int) -> int:
    """NFR-Perf-02: unprocessed message count for the dashboard."""
    key = stream_key(channel_id)
    try:
        groups = await redis.xinfo_groups(key)
    except Exception:  # noqa: BLE001 - stream may not exist yet
        return 0
    for g in groups:
        if g.get("name") == GROUP:
            lag = g.get("lag")
            pending = int(g.get("pending") or 0)
            return int(lag or 0) + pending
    return 0
