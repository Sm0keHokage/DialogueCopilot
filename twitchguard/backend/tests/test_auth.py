"""OAuth flow: AC-01, AC-02, AC-03 (tokens), AC-04."""
from __future__ import annotations

import inspect
import json
from collections.abc import Iterable

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import func, select

from twitchguard.models import Channel, Rule

from .conftest import FakeTwitch, login


def _iter_api_routes(routes: Iterable[object]) -> list[APIRoute]:
    """Flatten the FastAPI route tree into APIRoute leaves.

    Newer FastAPI versions may wrap included sub-routers lazily instead of
    flattening them into `app.routes` eagerly, so this walks both the plain
    `.routes` attribute (a regular APIRouter) and the `.original_router.routes`
    fallback (a lazily-included router) to stay robust across versions.
    """
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
            continue
        nested = getattr(route, "routes", None)
        if nested is None:
            nested = getattr(getattr(route, "original_router", None), "routes", None)
        if nested:
            found.extend(_iter_api_routes(nested))
    return found


async def test_openapi_has_no_password_or_2fa_inputs(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """AC-01 / NFR-Sec-01 / AR-01: outside /account/*, no route accepts a
    password, and /auth/twitch/* in particular never does anywhere in its
    path item (params, body, or otherwise) — Twitch credentials are typed on
    Twitch's own pages only. Local account passwords (личный кабинет) are a
    deliberately separate concern confined to /account/* (see accounts.py)."""
    spec = app.openapi()
    spec_text = json.dumps(spec).lower()
    assert "2fa" not in spec_text
    assert "otp" not in spec_text

    checked = 0
    for route in _iter_api_routes(app.routes):
        if route.path.startswith("/account"):
            continue
        body_field = route.body_field
        if body_field is None:
            continue
        checked += 1
        annotation = getattr(getattr(body_field, "field_info", None), "annotation", None)
        schema: dict[str, object] = {}
        if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
            schema = annotation.model_json_schema()
        schema_text = json.dumps(schema).lower()
        assert "password" not in schema_text, f"{route.path} request body has a password field"
    assert checked > 0, "sanity check: no non-/account route body was inspected"

    for path, operations in spec.get("paths", {}).items():
        if path.startswith("/auth/twitch"):
            assert "password" not in json.dumps(operations).lower()


async def test_login_redirects_to_twitch_authorize(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    resp = await client.get("/auth/twitch/login")
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://id.twitch.tv/oauth2/authorize")
    assert "client_id=test-client-id" in location
    assert "state=" in location
    assert "user%3Aread%3Achat" in location


async def test_state_mismatch_rejected(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """AC-02 / FR-06: wrong state -> 400, no channel created."""
    fake_twitch.add_login("code-x", "1", "owner", [])
    await client.get("/auth/twitch/login")
    resp = await client.get(
        "/auth/twitch/callback", params={"code": "code-x", "state": "forged-state"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "state_mismatch"
    async with app.state.sessionmaker() as db:
        count = (await db.execute(select(func.count(Channel.id)))).scalar_one()
    assert count == 0


async def test_access_denied_shows_error(client: httpx.AsyncClient) -> None:
    """UC-01 A2."""
    resp = await client.get("/auth/twitch/callback", params={"error": "access_denied"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "access_denied"


async def test_connect_flow_encrypts_tokens_and_seeds_rules(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """UC-01 + AC-03: tokens at rest are ciphertext; built-in rules seeded (FR-17)."""
    me = await login(client, fake_twitch)
    assert me["user"]["role"] == "owner"
    channel_id = me["channel"]["id"]
    plaintext_tokens = list(fake_twitch.tokens)
    async with app.state.sessionmaker() as db:
        channel = (
            await db.execute(select(Channel).where(Channel.id == channel_id))
        ).scalar_one()
        rules = list(
            (await db.execute(select(Rule).where(Rule.channel_id == channel_id))).scalars()
        )
    stored = channel.encrypted_access_token
    for token in plaintext_tokens:
        assert token.encode() not in stored
    # ...but decryption round-trips to a real issued token.
    assert app.state.cipher.decrypt(stored) in plaintext_tokens
    assert {r.name for r in rules} == {"spam", "toxicity"}


async def test_disconnect_revokes_and_wipes_tokens(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """AC-04 / FR-09 / IR-04."""
    me = await login(client, fake_twitch)
    channel_id = me["channel"]["id"]
    resp = await client.post(f"/channels/{channel_id}/disconnect")
    assert resp.status_code == 204
    assert len(fake_twitch.revoked) == 2  # access + refresh
    async with app.state.sessionmaker() as db:
        channel = (
            await db.execute(select(Channel).where(Channel.id == channel_id))
        ).scalar_one()
    assert channel.encrypted_access_token == b""
    assert channel.encrypted_refresh_token == b""
    assert channel.eventsub_status == "inactive"
    assert channel.needs_reauth is True


async def test_logout_kills_session(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    await login(client, fake_twitch)
    resp = await client.post("/auth/logout")
    assert resp.status_code == 204
    me = (await client.get("/auth/me")).json()
    assert me["authenticated"] is False


async def test_unauthenticated_api_call_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/channels/1/flags")
    assert resp.status_code == 401


@pytest.mark.parametrize("path", ["/channels/1/rules", "/channels/1/dashboard"])
async def test_foreign_channel_access_forbidden(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch, path: str
) -> None:
    """NFR-Sec-05: a member of channel A gets 403 on channel B."""
    me = await login(client, fake_twitch)
    other = me["channel"]["id"] + 999
    resp = await client.get(path.replace("/1/", f"/{other}/"))
    assert resp.status_code == 403
