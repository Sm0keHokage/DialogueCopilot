"""Flag lifecycle: AC-10 (state machine + audit), filters, pagination."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from twitchguard.flags import create_flag
from twitchguard.models import AuditLog

from .conftest import FakeTwitch, login


async def make_flag(app: FastAPI, channel_id: int, **overrides: object) -> int:
    params: dict = {
        "twitch_message_id": "m1",
        "author_login": "spammer",
        "author_id": "500",
        "message_text": "buy followers now",
        "rule_name": "spam",
        "rule_version": 1,
        "severity": "medium",
        "confidence": 0.92,
        "reason": "явная реклама",
        "action_hint": "delete",
    }
    params.update(overrides)
    async with app.state.sessionmaker() as db:
        flag = await create_flag(db, app.state.hub, channel_id=channel_id, **params)
        await db.commit()
        return flag.id


async def test_allowed_and_forbidden_transitions(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    flag_id = await make_flag(app, cid)

    resp = await client.patch(f"/channels/{cid}/flags/{flag_id}", json={"status": "reviewed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "reviewed"
    resp = await client.patch(f"/channels/{cid}/flags/{flag_id}", json={"status": "dismissed"})
    assert resp.status_code == 200

    # FR-52: dismissed is terminal -> 409 (AC-10).
    resp = await client.patch(f"/channels/{cid}/flags/{flag_id}", json={"status": "reviewed"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_transition"

    # FR-53: every transition is in the audit log.
    async with app.state.sessionmaker() as db:
        rows = list(
            (
                await db.execute(
                    select(AuditLog).where(AuditLog.action == "flag.status_changed")
                )
            ).scalars()
        )
    transitions = [(r.payload["from"], r.payload["to"]) for r in rows]
    assert ("new", "reviewed") in transitions
    assert ("reviewed", "dismissed") in transitions


async def test_unknown_status_rejected(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    flag_id = await make_flag(app, cid)
    resp = await client.patch(f"/channels/{cid}/flags/{flag_id}", json={"status": "actioned"})
    assert resp.status_code == 422  # actioned only through UC-05 (IR-11)


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"rule": "spam"}, {"m-spam"}),
        ({"severity": "high"}, {"m-tox"}),
        ({"author": "trolluser"}, {"m-tox"}),
        ({"status": "new"}, {"m-spam", "m-tox"}),
    ],
)
async def test_filters(
    app: FastAPI,
    client: httpx.AsyncClient,
    fake_twitch: FakeTwitch,
    params: dict,
    expected: set[str],
) -> None:
    """FR-37 / IR-10."""
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    await make_flag(app, cid, twitch_message_id="m-spam")
    await make_flag(
        app, cid, twitch_message_id="m-tox", rule_name="toxicity", severity="high",
        author_login="trolluser",
    )
    body = (await client.get(f"/channels/{cid}/flags", params=params)).json()
    assert {f["twitch_message_id"] for f in body["items"]} == expected


async def test_cursor_pagination(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    for i in range(5):
        await make_flag(app, cid, twitch_message_id=f"m{i}")
    page1 = (await client.get(f"/channels/{cid}/flags", params={"limit": 2})).json()
    assert len(page1["items"]) == 2
    assert page1["next_cursor"] is not None
    page2 = (
        await client.get(
            f"/channels/{cid}/flags", params={"limit": 2, "cursor": page1["next_cursor"]}
        )
    ).json()
    ids1 = {f["id"] for f in page1["items"]}
    ids2 = {f["id"] for f in page2["items"]}
    assert not ids1 & ids2
