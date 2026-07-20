"""Backend switching: AC-11, AC-12 (UC-06, FR-44..FR-48)."""
from __future__ import annotations

import httpx
from fastapi import FastAPI

from .conftest import FakeTwitch, login


async def test_switch_to_api_backend_and_key_never_returned(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    resp = await client.put(
        f"/channels/{cid}/settings/backend",
        json={"type": "api", "vendor": "anthropic", "api_key": "sk-ant-secret-123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["backend"]
    assert body["vendor"] == "anthropic"
    assert body["has_api_key"] is True
    assert "sk-ant-secret-123" not in resp.text  # FR-45 / AR-05
    settings = (await client.get(f"/channels/{cid}/settings")).json()
    assert "sk-ant-secret-123" not in str(settings)


async def test_invalid_key_keeps_previous_backend_active(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """AC-11 / FR-47."""
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    ok = await client.put(
        f"/channels/{cid}/settings/backend",
        json={"type": "api", "vendor": "anthropic", "api_key": "good-key"},
    )
    assert ok.status_code == 200
    fake_twitch.llm_status = 401  # vendor now rejects keys
    bad = await client.put(
        f"/channels/{cid}/settings/backend",
        json={"type": "api", "vendor": "openai", "api_key": "bad-key"},
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "backend_unavailable"
    settings = (await client.get(f"/channels/{cid}/settings")).json()
    assert settings["backend"]["vendor"] == "anthropic"  # old backend still active


async def test_cli_backend_requires_binary(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch, monkeypatch
) -> None:
    """FR-46: the missing binary is caught at save time; previous backend stays."""
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    monkeypatch.setattr("twitchguard.moderation.backends.cli.shutil.which", lambda _: None)
    resp = await client.put(
        f"/channels/{cid}/settings/backend", json={"type": "cli", "cli_tool": "claude"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "backend_unavailable"

    monkeypatch.setattr(
        "twitchguard.moderation.backends.cli.shutil.which", lambda _: "/usr/local/bin/claude"
    )
    resp = await client.put(
        f"/channels/{cid}/settings/backend", json={"type": "cli", "cli_tool": "claude"}
    )
    assert resp.status_code == 200
    assert resp.json()["backend"]["cli_tool"] == "claude"


async def test_deepseek_cli_combination_is_impossible(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """AC-12 / FR-48: DeepSeek is API-only — 'deepseek' is not a valid cli_tool."""
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    resp = await client.put(
        f"/channels/{cid}/settings/backend", json={"type": "cli", "cli_tool": "deepseek"}
    )
    assert resp.status_code == 422
    assert "cli_tool" in (resp.json()["error"]["field"] or "")


async def test_api_backend_requires_vendor_and_key(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    resp = await client.put(f"/channels/{cid}/settings/backend", json={"type": "api"})
    assert resp.status_code == 422
    resp = await client.put(
        f"/channels/{cid}/settings/backend", json={"type": "api", "vendor": "deepseek"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_api_key"


async def test_deepseek_as_api_backend_is_allowed(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """FR-48: DeepSeek is available — as an API backend."""
    me = await login(client, fake_twitch)
    cid = me["channel"]["id"]
    resp = await client.put(
        f"/channels/{cid}/settings/backend",
        json={"type": "api", "vendor": "deepseek", "api_key": "ds-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["backend"]["vendor"] == "deepseek"
