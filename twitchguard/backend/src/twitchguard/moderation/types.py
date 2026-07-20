"""Shared moderation datatypes."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChatMessage:
    message_id: str
    author_id: str
    author_login: str
    text: str
    ts_ms: int
    stream_id: str = ""  # redis stream entry id, for ack
