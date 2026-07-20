"""Classification pipeline: AC-05, AC-13, FR-13, FR-25..FR-31."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from twitchguard.models import AuditLog, Flag, Rule, Usage
from twitchguard.moderation.backends import register_backend
from twitchguard.moderation.backends.base import (
    BackendUnavailable,
    ClassifyResult,
    CompletionResult,
    ModelBackend,
)
from twitchguard.moderation.engine import run_cycle
from twitchguard.moderation.types import ChatMessage
from twitchguard.moderation.verdicts import Verdict

from .conftest import FakeTwitch, enqueue_message, login, set_backend_config

TEST_BACKEND = {"type": "api", "vendor": "test"}


class ScriptedBackend(ModelBackend):
    """classify() driven by a per-text script: {text_substring: verdict fields}."""

    kind = "test"

    def __init__(self, script: dict[str, dict[str, Any]]) -> None:
        super().__init__(cfg=None)  # type: ignore[arg-type]
        self.script = script
        self.classify_calls = 0
        self.fail_with: BackendUnavailable | None = None

    async def complete(self, prompt: str) -> CompletionResult:  # pragma: no cover
        raise NotImplementedError

    async def validate(self) -> None:
        return None

    async def classify(
        self, messages: Sequence[ChatMessage], rules: Sequence[Rule]
    ) -> ClassifyResult:
        self.classify_calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        verdicts = []
        for msg in messages:
            for key, fields in self.script.items():
                if key in msg.text:
                    verdicts.append(Verdict(message_id=msg.message_id, **fields))
        return ClassifyResult(verdicts=verdicts, tokens=10 * len(messages), requests=1)


async def _setup(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch, backend: ModelBackend
) -> int:
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    await set_backend_config(app, cid, TEST_BACKEND)
    register_backend("api:test", lambda ctx, cfg: backend)
    return cid


async def _flags(app: FastAPI, cid: int) -> list[Flag]:
    async with app.state.sessionmaker() as db:
        return list(
            (await db.execute(select(Flag).where(Flag.channel_id == cid))).scalars()
        )


async def _usage(app: FastAPI, cid: int) -> Usage | None:
    async with app.state.sessionmaker() as db:
        return (
            await db.execute(select(Usage).where(Usage.channel_id == cid))
        ).scalar_one_or_none()


async def test_violation_creates_flag_clean_message_does_not(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """AC-05: violation -> flag with rule/reason/confidence; clean -> nothing."""
    backend = ScriptedBackend(
        {"КУПИ ФОЛЛОВЕРОВ": {
            "rule": "spam", "is_violation": True, "confidence": 0.93,
            "reason": "реклама накрутки", "action_hint": "delete",
        }}
    )
    cid = await _setup(app, client, fake_twitch, backend)
    assert await enqueue_message(app, cid, "msg1", "КУПИ ФОЛЛОВЕРОВ на сайте x")
    assert await enqueue_message(app, cid, "msg2", "привет, отличный стрим!")
    processed = await run_cycle(app.state, cid)
    assert processed == 2
    flags = await _flags(app, cid)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.twitch_message_id == "msg1"
    assert flag.rule_name == "spam"
    assert flag.rule_version == 1
    assert flag.severity == "medium"
    assert flag.confidence > 0.9
    assert flag.reason == "реклама накрутки"
    assert flag.status == "new"
    usage = await _usage(app, cid)
    assert usage is not None
    assert usage.messages_processed == 2
    assert usage.flags_created == 1
    assert usage.tokens > 0


async def test_confidence_below_rule_threshold_is_not_flagged(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-26: spam threshold is 0.75 — a 0.5-confidence verdict is dropped."""
    backend = ScriptedBackend(
        {"maybe spam": {"rule": "spam", "is_violation": True, "confidence": 0.5,
                        "reason": "не уверен"}}
    )
    cid = await _setup(app, client, fake_twitch, backend)
    await enqueue_message(app, cid, "m1", "maybe spam?")
    await run_cycle(app.state, cid)
    assert await _flags(app, cid) == []


async def test_duplicate_message_id_deduplicated(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-13: EventSub redelivery is dropped by message_id."""
    backend = ScriptedBackend({})
    cid = await _setup(app, client, fake_twitch, backend)
    assert await enqueue_message(app, cid, "dup", "hello") is True
    assert await enqueue_message(app, cid, "dup", "hello") is False
    assert await run_cycle(app.state, cid) == 1


async def test_empty_and_system_messages_skipped(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-15."""
    backend = ScriptedBackend({})
    cid = await _setup(app, client, fake_twitch, backend)
    assert await enqueue_message(app, cid, "e1", "   ") is False
    assert await enqueue_message(app, cid, "", "text") is False


async def test_identical_spam_served_from_cache(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-30: copy-paste spam classified once, flagged twice."""
    backend = ScriptedBackend(
        {"buy cheap viewers": {"rule": "spam", "is_violation": True, "confidence": 0.9,
                               "reason": "spam ad"}}
    )
    cid = await _setup(app, client, fake_twitch, backend)
    await enqueue_message(app, cid, "c1", "buy cheap viewers today")
    await run_cycle(app.state, cid)
    await enqueue_message(app, cid, "c2", "buy cheap viewers today", author_login="other")
    await run_cycle(app.state, cid)
    assert backend.classify_calls == 1
    flags = await _flags(app, cid)
    assert {f.twitch_message_id for f in flags} == {"c1", "c2"}


async def test_backend_outage_keeps_messages_then_recovers(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """AC-13 / NFR-Rel-03: nothing is lost while the LLM is down."""
    backend = ScriptedBackend(
        {"toxic": {"rule": "toxicity", "is_violation": True, "confidence": 0.95,
                   "reason": "insult"}}
    )
    backend.fail_with = BackendUnavailable("backend_unreachable", "down")
    cid = await _setup(app, client, fake_twitch, backend)
    await enqueue_message(app, cid, "t1", "you are toxic trash")
    assert await run_cycle(app.state, cid) == 0
    assert await _flags(app, cid) == []
    backend.fail_with = None  # vendor is back
    assert await run_cycle(app.state, cid) == 1
    flags = await _flags(app, cid)
    assert len(flags) == 1
    assert flags[0].rule_name == "toxicity"


async def test_persistent_invalid_json_marks_classification_failed(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-25/FR-27: retries with corrective instruction, then failure is recorded."""

    class BrokenJsonBackend(ScriptedBackend):
        def __init__(self) -> None:
            super().__init__({})
            self.cfg = app.state.config.classifier
            self.completions = 0

        async def complete(self, prompt: str) -> CompletionResult:
            self.completions += 1
            return CompletionResult(text="oops, not json at all", tokens=3)

        async def classify(self, messages, rules):  # type: ignore[override]
            return await ModelBackend.classify(self, messages, rules)

    backend = BrokenJsonBackend()
    cid = await _setup(app, client, fake_twitch, backend)
    await enqueue_message(app, cid, "b1", "whatever")
    assert await run_cycle(app.state, cid) == 1
    assert backend.completions == app.state.config.classifier.max_retries + 1
    assert await _flags(app, cid) == []
    usage = await _usage(app, cid)
    assert usage is not None and usage.classification_failed == 1
    async with app.state.sessionmaker() as db:
        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "classification.failed")
            )
        ).scalar_one()
    assert audit.payload["message_ids"] == ["b1"]
    # The batch was dropped (FR-27), the service stays alive: nothing pending.
    assert await run_cycle(app.state, cid) == 0


async def test_invalid_json_then_valid_recovers_via_retry(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-25: one bad reply followed by a corrective retry that succeeds."""

    class FlakyBackend(ScriptedBackend):
        def __init__(self) -> None:
            super().__init__({})
            self.cfg = app.state.config.classifier
            self.replies = [
                "not json",
                '[{"message_id": "f1", "rule": "spam", "is_violation": true, '
                '"confidence": 0.9, "reason": "ad", "action_hint": null}]',
            ]

        async def complete(self, prompt: str) -> CompletionResult:
            return CompletionResult(text=self.replies.pop(0), tokens=2)

        async def classify(self, messages, rules):  # type: ignore[override]
            return await ModelBackend.classify(self, messages, rules)

    cid = await _setup(app, client, fake_twitch, FlakyBackend())
    await enqueue_message(app, cid, "f1", "spam text")
    await run_cycle(app.state, cid)
    flags = await _flags(app, cid)
    assert len(flags) == 1


async def test_language_scoped_rule_skips_other_language(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-31: an en-only rule does not flag a Russian message."""
    rule_md = """---
name: engonly
title: English only rule
enabled: true
severity: low
confidence_threshold: 0.5
languages: [en]
---
body
"""
    backend = ScriptedBackend(
        {"плохое": {"rule": "engonly", "is_violation": True, "confidence": 0.99,
                    "reason": "x"}}
    )
    cid = await _setup(app, client, fake_twitch, backend)
    resp = await client.post(f"/channels/{cid}/rules", json={"md_content": rule_md})
    assert resp.status_code == 201
    await enqueue_message(app, cid, "l1", "это очень плохое сообщение")
    await run_cycle(app.state, cid)
    assert await _flags(app, cid) == []


async def test_disabled_rules_stop_flagging_without_restart(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-20/FR-22/FR-23: rule set is re-read every batch."""
    backend = ScriptedBackend(
        {"spammy": {"rule": "spam", "is_violation": True, "confidence": 0.9, "reason": "r"}}
    )
    cid = await _setup(app, client, fake_twitch, backend)
    await client.patch(f"/channels/{cid}/rules/spam", json={"enabled": False})
    await enqueue_message(app, cid, "s1", "spammy text")
    await run_cycle(app.state, cid)
    assert await _flags(app, cid) == []  # verdict for a disabled rule is ignored
