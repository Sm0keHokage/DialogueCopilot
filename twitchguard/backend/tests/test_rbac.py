"""RBAC: AC-16 — a moderator cannot touch rules, settings or the moderator list."""
from __future__ import annotations

import httpx

from .conftest import FakeTwitch, login

RULE = """---
name: extra
title: Extra
enabled: true
severity: low
confidence_threshold: 0.9
---
body
"""


async def _owner_and_moderator(
    client: httpx.AsyncClient, client2: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> tuple[int, dict]:
    owner_me = await login(client, fake_twitch)
    cid = owner_me["channel"]["id"]
    resp = await client.post(f"/channels/{cid}/moderators", json={"login": "modnick"})
    assert resp.status_code == 201
    mod_me = await login(
        client2, fake_twitch, code="code-mod", user_id="200", login_name="modnick"
    )
    assert mod_me["user"]["role"] == "moderator"
    assert mod_me["channel"]["id"] == cid
    return cid, mod_me


async def test_moderator_permissions_matrix(
    client: httpx.AsyncClient, client2: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    cid, _ = await _owner_and_moderator(client, client2, fake_twitch)

    # Read access is allowed (FR-02: rules read-only, flags queue, dashboard).
    assert (await client2.get(f"/channels/{cid}/rules")).status_code == 200
    assert (await client2.get(f"/channels/{cid}/flags")).status_code == 200
    assert (await client2.get(f"/channels/{cid}/dashboard")).status_code == 200

    # Mutations of rules/settings/moderators are owner-only (AC-16).
    assert (
        await client2.post(f"/channels/{cid}/rules", json={"md_content": RULE})
    ).status_code == 403
    assert (
        await client2.patch(f"/channels/{cid}/rules/spam", json={"enabled": False})
    ).status_code == 403
    assert (await client2.get(f"/channels/{cid}/settings")).status_code == 403
    assert (
        await client2.put(
            f"/channels/{cid}/settings/backend",
            json={"type": "api", "vendor": "anthropic", "api_key": "k"},
        )
    ).status_code == 403
    assert (
        await client2.put(f"/channels/{cid}/settings/action-proxy", json={"enabled": True})
    ).status_code == 403
    assert (
        await client2.post(f"/channels/{cid}/moderators", json={"login": "x_y"})
    ).status_code == 403
    assert (await client2.post(f"/channels/{cid}/disconnect")).status_code == 403


async def test_owner_can_remove_moderator(
    client: httpx.AsyncClient, client2: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    cid, _ = await _owner_and_moderator(client, client2, fake_twitch)
    listing = (await client.get(f"/channels/{cid}/moderators")).json()
    assert listing["moderators"] == [{"login": "modnick", "registered": True}]
    assert (await client.delete(f"/channels/{cid}/moderators/modnick")).status_code == 204
    listing = (await client.get(f"/channels/{cid}/moderators")).json()
    assert listing["moderators"] == []
