"""Parallel AI agents per channel: sharding, stale-reclaim, settings, hot restart.

One Twitch reader account is used regardless of agent count (AR-03) — only the
classification stage fans out over a Redis consumer group.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from twitchguard.models import Channel, Flag, Usage
from twitchguard.moderation.backends.base import BackendUnavailable
from twitchguard.moderation.engine import run_cycle
from twitchguard.pipelines import (
    classifier_task_prefix,
    restart_classifier_workers,
)

from .conftest import FakeTwitch, enqueue_message, login
from .test_classifier import ScriptedBackend, _setup

VIOLATION = {
    "rule": "spam",
    "is_violation": True,
    "confidence": 0.9,
    "reason": "реклама",
}


async def _channel(app: FastAPI, cid: int) -> Channel:
    async with app.state.sessionmaker() as db:
        return (await db.execute(select(Channel).where(Channel.id == cid))).scalar_one()


async def test_put_workers_validates_and_persists(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    max_workers = app.state.config.classifier.max_workers

    resp = await client.put(f"/channels/{cid}/settings/workers", json={"workers": 4})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"workers": 4, "max_workers": max_workers, "active": 0}

    settings = (await client.get(f"/channels/{cid}/settings")).json()
    assert settings["classifier_workers"] == 4
    assert settings["max_workers"] == max_workers

    assert (
        await client.put(f"/channels/{cid}/settings/workers", json={"workers": 0})
    ).status_code == 422
    over = await client.put(
        f"/channels/{cid}/settings/workers", json={"workers": max_workers + 1}
    )
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "too_many_workers"
    # The failed updates did not clobber the stored value.
    assert (await _channel(app, cid)).classifier_workers == 4


async def test_moderator_cannot_change_workers(
    client: httpx.AsyncClient, client2: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    await client.post(f"/channels/{cid}/moderators", json={"login": "modnick"})
    await login(client2, fake_twitch, code="code-mod", user_id="200", login_name="modnick")
    resp = await client2.put(f"/channels/{cid}/settings/workers", json={"workers": 2})
    assert resp.status_code == 403


async def test_consumer_group_shards_between_agents_without_double_processing(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """Two agents split the stream; every message is classified exactly once."""
    backend = ScriptedBackend({"спам": VIOLATION})
    cid = await _setup(app, client, fake_twitch, backend)
    app.state.config.classifier.batch_size = 2

    for i in range(3):
        await enqueue_message(app, cid, f"w{i}", f"спам номер {i}")

    first = await run_cycle(app.state, cid, consumer="worker-1")
    second = await run_cycle(app.state, cid, consumer="worker-2")
    assert first == 2
    assert second == 1
    assert backend.classify_calls == 2  # one LLM call per agent batch

    async with app.state.sessionmaker() as db:
        flags = list(
            (await db.execute(select(Flag).where(Flag.channel_id == cid))).scalars()
        )
        usage = (
            await db.execute(select(Usage).where(Usage.channel_id == cid))
        ).scalar_one()
    assert sorted(f.twitch_message_id for f in flags) == ["w0", "w1", "w2"]
    assert usage.messages_processed == 3  # no double counting across agents
    assert usage.flags_created == 3


async def test_dead_agent_pending_is_reclaimed_by_sibling(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """worker-1 took a message and died mid-flight; worker-2 rescues it."""
    backend = ScriptedBackend({"токсик": VIOLATION | {"rule": "toxicity"}})
    cid = await _setup(app, client, fake_twitch, backend)
    app.state.config.classifier.reclaim_idle_ms = 0  # everything counts as stale

    await enqueue_message(app, cid, "r1", "ну ты и токсик")
    backend.fail_with = BackendUnavailable("backend_unreachable", "down")
    assert await run_cycle(app.state, cid, consumer="worker-1") == 0  # stays pending

    backend.fail_with = None
    assert await run_cycle(app.state, cid, consumer="worker-2") == 1
    async with app.state.sessionmaker() as db:
        flags = list(
            (await db.execute(select(Flag).where(Flag.channel_id == cid))).scalars()
        )
    assert len(flags) == 1
    assert flags[0].twitch_message_id == "r1"
    # And it is fully acked — no agent sees it again.
    assert await run_cycle(app.state, cid, consumer="worker-1") == 0
    assert await run_cycle(app.state, cid, consumer="worker-2") == 0


async def test_restart_applies_new_agent_count_hot(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch, monkeypatch
) -> None:
    # Task management is under test, not the loop body: the real loop hammers
    # the shared in-memory SQLite connection from N background tasks.
    import asyncio

    async def idle_loop(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr("twitchguard.pipelines.classifier_loop", idle_loop)
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    channel = await _channel(app, cid)
    supervisor = app.state.supervisor

    channel.classifier_workers = 3
    assert await restart_classifier_workers(app.state, channel) == 3
    assert supervisor.running_names(classifier_task_prefix(cid)) == [
        f"classifier:{cid}:1",
        f"classifier:{cid}:2",
        f"classifier:{cid}:3",
    ]

    channel.classifier_workers = 1
    assert await restart_classifier_workers(app.state, channel) == 1
    assert supervisor.running_names(classifier_task_prefix(cid)) == [f"classifier:{cid}:1"]

    # The count is clamped to classifier.max_workers.
    channel.classifier_workers = 999
    assert (
        await restart_classifier_workers(app.state, channel)
        == app.state.config.classifier.max_workers
    )
    await supervisor.stop_prefix(classifier_task_prefix(cid))


async def test_dashboard_reports_worker_counts(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    await client.put(f"/channels/{cid}/settings/workers", json={"workers": 2})
    body = (await client.get(f"/channels/{cid}/dashboard")).json()
    # start_workers=False in tests: configured 2, active 0.
    assert body["workers"] == {"configured": 2, "active": 0}
