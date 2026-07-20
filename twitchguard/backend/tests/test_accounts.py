"""Local account system (личный кабинет): registration, verification,
brute-force protection, password change, logout-everywhere, Twitch linking.
"""
from __future__ import annotations

import re
import urllib.parse

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select

from twitchguard.models import Account, User

from .conftest import FakeTwitch, login

REGISTER_MSG = {"message": "Проверьте почту — мы отправили ссылку подтверждения"}
_TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")


def _extract_token(body_text: str) -> str:
    match = _TOKEN_RE.search(body_text)
    assert match, f"no token= in mail body: {body_text!r}"
    return match.group(1)


async def _register(
    client: httpx.AsyncClient,
    *,
    email: str = "Viewer@Example.com",
    nick: str = "Viewer_1",
    password: str = "hunter22",
) -> dict[str, str]:
    body = {"email": email, "nick": nick, "password": password}
    resp = await client.post("/account/register", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json() == REGISTER_MSG
    return body


async def _register_verify_login(
    app: FastAPI,
    client: httpx.AsyncClient,
    **kwargs: str,
) -> dict[str, str]:
    body = await _register(client, **kwargs)
    token = _extract_token(app.state.emailer.outbox[-1]["body"])
    resp = await client.post("/account/verify", json={"token": token})
    assert resp.status_code == 200
    resp = await client.post(
        "/account/login", json={"login": body["nick"], "password": body["password"]}
    )
    assert resp.status_code == 200
    return body


async def test_register_verify_login_flow(app: FastAPI, client: httpx.AsyncClient) -> None:
    body = await _register(client)
    assert app.state.emailer.outbox, "registration must queue a verification email"
    mail = app.state.emailer.outbox[-1]
    assert mail["to"] == body["email"].lower()
    assert mail["subject"]
    token = _extract_token(mail["body"])

    resp = await client.post("/account/verify", json={"token": token})
    assert resp.status_code == 200
    assert resp.json() == {"verified": True}

    resp = await client.post(
        "/account/login", json={"login": body["nick"], "password": body["password"]}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    me = (await client.get("/auth/me")).json()
    assert me["authenticated"] is True
    assert me["twitch_linked"] is False
    assert me["user"] is None
    assert me["channel"] is None
    assert me["account"]["nick"] == body["nick"].lower()
    assert me["account"]["email"] == body["email"].lower()
    assert me["account"]["email_verified"] is True


async def test_duplicate_register_is_silent(app: FastAPI, client: httpx.AsyncClient) -> None:
    body = await _register(client, email="dup@example.com", nick="dupnick")
    sent_after_first = len(app.state.emailer.outbox)

    # Same email, different nick.
    resp = await client.post(
        "/account/register",
        json={"email": body["email"], "nick": "someoneelse", "password": "another-pw"},
    )
    assert resp.status_code == 201
    assert resp.json() == REGISTER_MSG

    # Same nick, different email.
    resp = await client.post(
        "/account/register",
        json={"email": "someoneelse@example.com", "nick": body["nick"], "password": "another-pw"},
    )
    assert resp.status_code == 201
    assert resp.json() == REGISTER_MSG

    # No mail sent for either silently-rejected duplicate, and only one row.
    assert len(app.state.emailer.outbox) == sent_after_first
    async with app.state.sessionmaker() as db:
        count = (await db.execute(select(func.count(Account.id)))).scalar_one()
    assert count == 1


async def test_register_validates_input(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/account/register",
        json={"email": "not-an-email", "nick": "validnick", "password": "longenough"},
    )
    assert resp.status_code == 422

    resp = await client.post(
        "/account/register",
        json={"email": "a@b.com", "nick": "n", "password": "longenough"},
    )
    assert resp.status_code == 422

    resp = await client.post(
        "/account/register",
        json={"email": "a@b.com", "nick": "validnick", "password": "short"},
    )
    assert resp.status_code == 422


async def test_verify_invalid_token_is_400(client: httpx.AsyncClient) -> None:
    resp = await client.post("/account/verify", json={"token": "not-a-real-token"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_token"


async def test_login_allowed_before_verification(app: FastAPI, client: httpx.AsyncClient) -> None:
    body = await _register(client)
    resp = await client.post(
        "/account/login", json={"login": body["email"], "password": body["password"]}
    )
    assert resp.status_code == 200

    me = (await client.get("/auth/me")).json()
    assert me["authenticated"] is True
    assert me["account"]["email_verified"] is False


async def test_resend_rotates_token_and_rate_limits(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    body = await _register(client)
    first_token = _extract_token(app.state.emailer.outbox[-1]["body"])
    sent_after_register = len(app.state.emailer.outbox)

    resp = await client.post("/account/resend", json={"email": body["email"]})
    assert resp.status_code == 200
    assert len(app.state.emailer.outbox) == sent_after_register + 1
    second_token = _extract_token(app.state.emailer.outbox[-1]["body"])
    assert second_token != first_token

    # The old token no longer verifies; the rotated one does.
    resp = await client.post("/account/verify", json={"token": first_token})
    assert resp.status_code == 400
    resp = await client.post("/account/verify", json={"token": second_token})
    assert resp.status_code == 200

    # Immediate second resend for the same address is rate-limited (no mail).
    resp = await client.post("/account/resend", json={"email": body["email"]})
    assert resp.status_code == 200
    assert len(app.state.emailer.outbox) == sent_after_register + 1

    # Unknown address: same generic response, no mail, no error.
    resp = await client.post("/account/resend", json={"email": "nobody@example.com"})
    assert resp.status_code == 200
    assert len(app.state.emailer.outbox) == sent_after_register + 1


async def test_brute_force_lockout(client: httpx.AsyncClient) -> None:
    body = await _register(client)
    statuses = []
    for _ in range(6):
        resp = await client.post(
            "/account/login", json={"login": body["nick"], "password": "totally-wrong"}
        )
        statuses.append(resp.status_code)
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429

    resp = await client.post(
        "/account/login", json={"login": body["nick"], "password": "totally-wrong"}
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "too_many_attempts"


async def test_change_password_requires_account_session(client2: httpx.AsyncClient) -> None:
    resp = await client2.post(
        "/account/password",
        json={"current_password": "whatever1", "new_password": "whatever2"},
    )
    assert resp.status_code == 401


async def test_change_password_wrong_current_is_403(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    body = await _register_verify_login(app, client)
    resp = await client.post(
        "/account/password",
        json={"current_password": "not-the-current-one", "new_password": "brand-new-pw-2"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "wrong_password"
    # Old password still works — nothing changed.
    resp = await client.post(
        "/account/login", json={"login": body["nick"], "password": body["password"]}
    )
    assert resp.status_code == 200


async def test_password_change_invalidates_other_sessions(
    app: FastAPI, client: httpx.AsyncClient, client2: httpx.AsyncClient
) -> None:
    body = await _register_verify_login(app, client)

    # A second browser (client2) logs into the same account.
    resp = await client2.post(
        "/account/login", json={"login": body["nick"], "password": body["password"]}
    )
    assert resp.status_code == 200
    assert (await client2.get("/auth/me")).json()["authenticated"] is True

    resp = await client.post(
        "/account/password",
        json={"current_password": body["password"], "new_password": "brand-new-pw-2"},
    )
    assert resp.status_code == 204

    # client2's session is dead; client's own (kept) session still works.
    assert (await client2.get("/auth/me")).json()["authenticated"] is False
    assert (await client.get("/auth/me")).json()["authenticated"] is True

    # The old password is rejected, the new one works.
    resp = await client.post(
        "/account/login", json={"login": body["nick"], "password": body["password"]}
    )
    assert resp.status_code == 401
    resp = await client.post(
        "/account/login", json={"login": body["nick"], "password": "brand-new-pw-2"}
    )
    assert resp.status_code == 200


async def test_logout_all_kills_every_session(
    app: FastAPI, client: httpx.AsyncClient, client2: httpx.AsyncClient
) -> None:
    body = await _register_verify_login(app, client)
    resp = await client2.post(
        "/account/login", json={"login": body["nick"], "password": body["password"]}
    )
    assert resp.status_code == 200

    resp = await client.post("/account/logout-all")
    assert resp.status_code == 204

    assert (await client.get("/auth/me")).json()["authenticated"] is False
    assert (await client2.get("/auth/me")).json()["authenticated"] is False


async def test_link_twitch_after_verified_account_login(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    body = await _register_verify_login(app, client)

    me = await login(client, fake_twitch)  # OAuth dance on the same cookie jar
    assert me["twitch_linked"] is True
    assert me["channel"] is not None
    assert me["account"] is not None
    assert me["account"]["nick"] == body["nick"].lower()
    assert me["account"]["email_verified"] is True

    async with app.state.sessionmaker() as db:
        user = (await db.execute(select(User).where(User.id == me["user"]["id"]))).scalar_one()
    assert user.account_id is not None


async def test_oauth_link_blocked_when_email_unverified(
    client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    body = await _register(client)
    resp = await client.post(
        "/account/login", json={"login": body["nick"], "password": body["password"]}
    )
    assert resp.status_code == 200

    fake_twitch.add_login("code-unverified", "900", "someviewer", [])
    resp = await client.get("/auth/twitch/login")
    assert resp.status_code == 302
    query = urllib.parse.parse_qs(urllib.parse.urlparse(resp.headers["location"]).query)
    state = query["state"][0]
    resp = await client.get(
        "/auth/twitch/callback", params={"code": "code-unverified", "state": state}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "email_not_verified"


async def test_security_headers_present(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["x-frame-options"] == "DENY"
    assert "cache-control" not in resp.headers

    resp = await client.get("/auth/me")
    assert resp.headers["cache-control"] == "no-store"

    resp = await client.post("/account/verify", json={"token": "x"})
    assert resp.headers["cache-control"] == "no-store"
