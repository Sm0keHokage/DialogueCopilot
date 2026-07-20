"""Realtime queue: FR-33 (live push), FR-39 (snapshot restore), IR-17, AC-15."""
from __future__ import annotations

import urllib.parse

from fastapi import Request
from starlette.testclient import TestClient

from twitchguard.flags import create_flag, flag_out
from twitchguard.twitch.oauth import READ_SCOPES

from .conftest import FakeTwitch, build_app


def _sync_login(tc: TestClient, fake: FakeTwitch) -> int:
    fake.add_login("code-owner", "100", "owner", list(READ_SCOPES))
    resp = tc.get("/auth/twitch/login", follow_redirects=False)
    assert resp.status_code == 302
    state = urllib.parse.parse_qs(
        urllib.parse.urlparse(resp.headers["location"]).query
    )["state"][0]
    resp = tc.get(
        "/auth/twitch/callback",
        params={"code": "code-owner", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    me = tc.get("/auth/me").json()
    return int(me["channel"]["id"])


def test_ws_snapshot_live_push_and_rbac() -> None:
    app, fake = build_app()

    # Test-only trigger that exercises the production create_flag + broadcast path.
    @app.post("/_test/channels/{channel_id}/flags")
    async def _make_flag(channel_id: int, request: Request) -> dict:
        async with request.app.state.sessionmaker() as db:
            flag = await create_flag(
                db,
                request.app.state.hub,
                channel_id=channel_id,
                twitch_message_id=f"m-{id(request)}",
                author_login="spammer",
                author_id="500",
                message_text="buy followers",
                rule_name="spam",
                rule_version=1,
                severity="medium",
                confidence=0.9,
                reason="ad",
                action_hint="delete",
            )
            await db.commit()
            return flag_out(flag)

    with TestClient(app) as tc:
        cid = _sync_login(tc, fake)
        first = tc.post(f"/_test/channels/{cid}/flags").json()
        cookie = tc.cookies.get("tg_session")
        assert cookie

        with tc.websocket_connect(
            f"/channels/{cid}/stream", headers={"cookie": f"tg_session={cookie}"}
        ) as ws:
            # FR-39: snapshot restores the queue on connect.
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            assert [f["id"] for f in snapshot["data"]["flags"]] == [first["id"]]

            # FR-33 / AC-15: a new flag arrives live.
            second = tc.post(f"/_test/channels/{cid}/flags").json()
            event = ws.receive_json()
            assert event["type"] == "flag.created"
            assert event["data"]["id"] == second["id"]

            # flag.updated is pushed on status change (IR-17).
            tc.patch(f"/channels/{cid}/flags/{first['id']}", json={"status": "dismissed"})
            event = ws.receive_json()
            assert event["type"] == "flag.updated"
            assert event["data"]["status"] == "dismissed"

        # A forged session cookie -> connection is refused (NFR-Sec-05).
        # Depending on the Starlette version the 4403 close surfaces on connect
        # or on the first receive — both count as denial.
        denied = False
        try:
            with tc.websocket_connect(
                f"/channels/{cid}/stream",
                headers={"cookie": "tg_session=forged-sid.badsignature"},
            ) as anon_ws:
                try:
                    anon_ws.receive_json()
                except Exception as exc:  # noqa: BLE001
                    denied = getattr(exc, "code", 4403) == 4403
        except Exception as exc:  # noqa: BLE001
            denied = getattr(exc, "code", 4403) == 4403
        assert denied


def test_dashboard_endpoint_shape() -> None:
    app, fake = build_app()
    with TestClient(app) as tc:
        cid = _sync_login(tc, fake)
        body = tc.get(f"/channels/{cid}/dashboard").json()
        assert body["channel"]["eventsub_status"] == "inactive"
        assert body["backend"]["configured"] is False
        assert body["today"]["messages_processed"] == 0
        assert "p50" in body["latency_ms"]
        assert body["backlog"] == 0
        assert body["precision"] == []
