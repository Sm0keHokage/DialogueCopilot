"""Structured JSON logging with secret redaction (FR-50, NFR-Sec-03, AR-05)."""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_SECRETS: set[str] = set()

_KEY_VALUE_RE = re.compile(
    r"(?i)\b(access_token|refresh_token|api_key|client_secret|authorization|password|token)"
    r"(\"?\'?\s*[:=]\s*)(\"?)([^\s\"',}]+)"
)


def register_secret(value: str | None) -> None:
    """Register a literal secret value so it is masked in every log line."""
    if value and len(value) >= 6:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for secret in _SECRETS:
        if secret in text:
            text = text.replace(secret, "***")
    return _KEY_VALUE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}***", text)


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = None
        except Exception:  # noqa: BLE001 - never lose a log line to redaction
            pass
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def make_handler(stream: Any = None) -> logging.Handler:
    """A JSON handler with redaction; the filter sits on the handler because
    logger-level filters do not apply to records propagated from child loggers."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    return handler


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(getattr(h, "_twitchguard", False) for h in root.handlers):
        handler = make_handler()
        handler._twitchguard = True  # type: ignore[attr-defined]
        root.addHandler(handler)
