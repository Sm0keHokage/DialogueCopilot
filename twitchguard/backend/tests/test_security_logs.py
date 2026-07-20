"""AC-03 / NFR-Sec-03: the log pipeline never emits tokens, keys or client_secret."""
from __future__ import annotations

import io
import logging

import httpx
from fastapi import FastAPI

from twitchguard.logging_setup import make_handler, redact, register_secret

from .conftest import FakeTwitch, login


def test_redact_masks_registered_secrets_and_kv_patterns() -> None:
    register_secret("super-secret-token-value")
    assert "super-secret-token-value" not in redact("token super-secret-token-value here")
    line = redact('payload access_token="abcdef123456"')
    assert "abcdef123456" not in line
    line = redact("client_secret=verysecret123 sent")
    assert "verysecret123" not in line


async def test_full_oauth_flow_leaves_no_secrets_in_log_output(
    app: FastAPI, client: httpx.AsyncClient, fake_twitch: FakeTwitch
) -> None:
    """Grep-style CI check (AC-03): run the real flow, capture logs, grep for secrets."""
    stream = io.StringIO()
    handler = make_handler(stream)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        await login(client, fake_twitch)
        # Simulate sloppy logging of sensitive values from any module.
        issued_tokens = list(fake_twitch.tokens)
        logging.getLogger("twitchguard.sloppy").info(
            "exchange done access_token=%s client_secret=%s",
            issued_tokens[0],
            app.state.settings.twitch_client_secret,
        )
    finally:
        root.removeHandler(handler)
    output = stream.getvalue()
    assert output  # the pipeline did log something
    for token in issued_tokens:
        assert token not in output
    assert app.state.settings.twitch_client_secret not in output
    assert app.state.settings.encryption_key not in output
