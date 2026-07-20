"""Action Proxy: AC-09 (human-only actions, audit, scope gating, idempotency)."""
from __future__ import annotations

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from twitchguard.models import AuditLog

from .conftest import FakeTwitch, login
from .test_flags import make_flag


async def test_action_forbidden_when_proxy_disabled(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-40(а): advisory by default — no proxy, no action."""
    me = await login(client, fake_twitch, action_scopes=True)
    cid = me["channel"]["id"]
    flag_id = await make_flag(app, cid)
    resp = await client.post(
        f"/channels/{cid}/flags/{flag_id}/action", json={"type": "delete"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "action_proxy_disabled"


async def test_action_forbidden_without_scopes(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-40(б)/FR-55: proxy on, but the token lacks the moderation scopes."""
    me = await login(client, fake_twitch, action_scopes=False)
    cid = me["channel"]["id"]
    toggled = (
        await client.put(f"/channels/{cid}/settings/action-proxy", json={"enabled": True})
    ).json()
    assert toggled["reauth_required"] is True  # IR-15: points at scope re-request
    assert toggled["reauth_url"]
    flag_id = await make_flag(app, cid)
    resp = await client.post(
        f"/channels/{cid}/flags/{flag_id}/action", json={"type": "ban"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "missing_scope"


async def _enable_proxy(client: httpx.AsyncClient, cid: int) -> None:
    resp = await client.put(f"/channels/{cid}/settings/action-proxy", json={"enabled": True})
    assert resp.json()["reauth_required"] is False


async def test_delete_action_runs_under_moderator_token_and_audits(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """AC-09 / FR-41 / FR-43 / FR-54."""
    me = await login(client, fake_twitch, action_scopes=True)
    cid = me["channel"]["id"]
    await _enable_proxy(client, cid)
    flag_id = await make_flag(app, cid)
    resp = await client.post(
        f"/channels/{cid}/flags/{flag_id}/action", json={"type": "delete"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "actioned"
    call = fake_twitch.helix_calls[-1]
    assert call["kind"] == "delete"
    # FR-54: moderator_id is the human's Twitch id (owner "100" here), never a bot.
    assert call["params"]["moderator_id"] == "100"
    assert call["params"]["broadcaster_id"] == "100"
    async with app.state.sessionmaker() as db:
        audit = (
            await db.execute(select(AuditLog).where(AuditLog.action == "action.applied"))
        ).scalar_one()
    assert audit.actor_type == "user"
    assert audit.payload["type"] == "delete"


async def test_timeout_requires_duration_and_passes_it_to_helix(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    me = await login(client, fake_twitch, action_scopes=True)
    cid = me["channel"]["id"]
    await _enable_proxy(client, cid)
    flag_id = await make_flag(app, cid)
    resp = await client.post(
        f"/channels/{cid}/flags/{flag_id}/action", json={"type": "timeout"}
    )
    assert resp.status_code == 422
    resp = await client.post(
        f"/channels/{cid}/flags/{flag_id}/action", json={"type": "timeout", "duration_s": 600}
    )
    assert resp.status_code == 200
    call = fake_twitch.helix_calls[-1]
    assert call["kind"] == "ban"
    assert call["body"]["data"]["duration"] == 600
    assert call["body"]["data"]["user_id"] == "500"


async def test_helix_error_keeps_flag_status_and_audits_failure(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-42 / UC-05 A1."""
    me = await login(client, fake_twitch, action_scopes=True)
    cid = me["channel"]["id"]
    await _enable_proxy(client, cid)
    flag_id = await make_flag(app, cid)
    fake_twitch.helix_error = (400, "user is already banned")
    resp = await client.post(
        f"/channels/{cid}/flags/{flag_id}/action", json={"type": "ban"}
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "helix_error"
    body = (await client.get(f"/channels/{cid}/flags")).json()
    flag = next(f for f in body["items"] if f["id"] == flag_id)
    assert flag["status"] == "new"  # not actioned
    async with app.state.sessionmaker() as db:
        audit = (
            await db.execute(select(AuditLog).where(AuditLog.action == "action.failed"))
        ).scalar_one()
    assert audit.payload["helix_status"] == 400


async def test_repeat_action_on_actioned_flag_is_409(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-56: idempotency."""
    me = await login(client, fake_twitch, action_scopes=True)
    cid = me["channel"]["id"]
    await _enable_proxy(client, cid)
    flag_id = await make_flag(app, cid)
    first = await client.post(
        f"/channels/{cid}/flags/{flag_id}/action", json={"type": "delete"}
    )
    assert first.status_code == 200
    second = await client.post(
        f"/channels/{cid}/flags/{flag_id}/action", json={"type": "delete"}
    )
    assert second.status_code == 409
