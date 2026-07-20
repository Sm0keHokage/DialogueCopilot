"""Rules: AC-06, AC-07 (upload, validation, versions, hot toggle)."""
from __future__ import annotations

import httpx
from fastapi import FastAPI

from twitchguard.rules.service import get_active_rules

from .conftest import FakeTwitch, login

VALID_RULE = """---
name: caps
title: Капс
enabled: true
severity: low
confidence_threshold: 0.9
action_hint: warn
languages: [ru, en]
---

## Что считать нарушением
- Сообщение целиком капсом длиннее 20 символов.

## Что НЕ нарушение
- Короткие эмоциональные выкрики.
"""

BROKEN_RULE = """---
name: broken
title: Без severity
enabled: true
confidence_threshold: 1.5
---
body
"""


async def test_upload_valid_rule(client: httpx.AsyncClient, fake_twitch: FakeTwitch) -> None:
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    resp = await client.post(f"/channels/{cid}/rules", json={"md_content": VALID_RULE})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "caps"
    assert body["version"] == 1
    names = {r["name"] for r in (await client.get(f"/channels/{cid}/rules")).json()}
    assert names == {"spam", "toxicity", "caps"}


async def test_invalid_rule_rejected_with_field_and_others_keep_working(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """AC-06 / FR-19: 422 names the field; existing rules unaffected."""
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    resp = await client.post(f"/channels/{cid}/rules", json={"md_content": BROKEN_RULE})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "invalid_rule"
    fields = {d["field"] for d in err["details"]}
    assert "severity" in fields
    assert "confidence_threshold" in fields
    async with app.state.sessionmaker() as db:
        active = await get_active_rules(db, cid)
    assert {r.name for r in active} == {"spam", "toxicity"}


async def test_validate_endpoint_previews_without_saving(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """IR-06 / UC-02 step 3."""
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    ok = (
        await client.post(f"/channels/{cid}/rules/validate", json={"md_content": VALID_RULE})
    ).json()
    assert ok["valid"] is True
    assert ok["frontmatter"]["severity"] == "low"
    bad = (
        await client.post(f"/channels/{cid}/rules/validate", json={"md_content": "no frontmatter"})
    ).json()
    assert bad["valid"] is False
    assert bad["errors"][0]["field"] == "frontmatter"
    names = {r["name"] for r in (await client.get(f"/channels/{cid}/rules")).json()}
    assert "caps" not in names


async def test_saving_same_name_creates_new_version(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """AC-07 / FR-21: version history is preserved."""
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    await client.post(f"/channels/{cid}/rules", json={"md_content": VALID_RULE})
    v2 = VALID_RULE.replace("confidence_threshold: 0.9", "confidence_threshold: 0.7")
    resp = await client.post(f"/channels/{cid}/rules", json={"md_content": v2})
    assert resp.status_code == 201
    assert resp.json()["version"] == 2
    versions = (await client.get(f"/channels/{cid}/rules/caps/versions")).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert [v["is_current"] for v in versions] == [True, False]


async def test_toggle_enabled_without_delete(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-22 + hot reload point (FR-20): the engine reads the column live."""
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    resp = await client.patch(f"/channels/{cid}/rules/spam", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    async with app.state.sessionmaker() as db:
        active = {r.name for r in await get_active_rules(db, cid)}
    assert active == {"toxicity"}


async def test_patch_md_with_mismatched_name_rejected(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    resp = await client.patch(
        f"/channels/{cid}/rules/spam", json={"md_content": VALID_RULE}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "name_mismatch"
