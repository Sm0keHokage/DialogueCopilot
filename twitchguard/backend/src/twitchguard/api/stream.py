"""Realtime WebSocket endpoint (IR-17): snapshot + live events."""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..flags import flag_out, snapshot_flags
from ..models import Channel
from ..rbac import ROLE_MODERATOR, ROLE_OWNER

router = APIRouter()


@router.websocket("/channels/{channel_id}/stream")
async def stream(ws: WebSocket, channel_id: int) -> None:
    app_state = ws.app.state
    cookie = ws.cookies.get(app_state.settings.session_cookie_name)
    session = await app_state.sessions.load(cookie)
    if (
        session is None
        or int(session.data.get("channel_id", -1)) != channel_id
        or session.data.get("role") not in (ROLE_OWNER, ROLE_MODERATOR)
    ):
        # NFR-Sec-05 applies to the WS endpoint too.
        await ws.close(code=4403)
        return
    await ws.accept()
    hub = app_state.hub
    await hub.connect(channel_id, ws)
    try:
        # IR-17 / FR-39: the client gets the current queue state on connect.
        async with app_state.sessionmaker() as db:
            flags = await snapshot_flags(db, channel_id)
            from sqlalchemy import select

            channel = (
                await db.execute(select(Channel).where(Channel.id == channel_id))
            ).scalar_one_or_none()
        await ws.send_text(
            json.dumps(
                {
                    "type": "snapshot",
                    "data": {
                        "flags": [flag_out(f) for f in flags],
                        "channel": {
                            "eventsub_status": channel.eventsub_status if channel else "inactive",
                            "needs_reauth": channel.needs_reauth if channel else False,
                        },
                    },
                },
                default=str,
                ensure_ascii=False,
            )
        )
        while True:
            await ws.receive_text()  # keepalive pings from the client; content ignored
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(channel_id, ws)
