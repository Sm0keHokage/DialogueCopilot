"""Backend registry and factory (FR-44, FR-48, UC-06)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ...config import ClassifierConfig
from ...crypto import TokenCipher
from .api import AnthropicBackend, DeepSeekBackend, OpenAIBackend
from .base import BackendUnavailable, ModelBackend
from .cli import CLI_COMMANDS, CLIBackend

API_VENDORS = ("anthropic", "openai", "deepseek")
CLI_TOOLS = tuple(CLI_COMMANDS)  # deepseek intentionally absent (FR-48, AC-12)


@dataclass
class BackendContext:
    cfg: ClassifierConfig
    http: httpx.AsyncClient
    cipher: TokenCipher


BackendFactory = Callable[[BackendContext, dict[str, Any]], ModelBackend]

_API_CLASSES: dict[str, type] = {
    "anthropic": AnthropicBackend,
    "openai": OpenAIBackend,
    "deepseek": DeepSeekBackend,
}

_EXTRA_BACKENDS: dict[str, BackendFactory] = {}


def register_backend(key: str, factory: BackendFactory) -> None:
    """Extension hook (used by the test suite to plug a fake backend)."""
    _EXTRA_BACKENDS[key] = factory


def build_backend(ctx: BackendContext, backend_config: dict[str, Any]) -> ModelBackend:
    """Build a backend from a channel's backend_config; raises BackendUnavailable."""
    btype = str(backend_config.get("type") or "")
    if btype == "api":
        vendor = str(backend_config.get("vendor") or "")
        if f"api:{vendor}" in _EXTRA_BACKENDS:
            return _EXTRA_BACKENDS[f"api:{vendor}"](ctx, backend_config)
        cls = _API_CLASSES.get(vendor)
        if cls is None:
            raise BackendUnavailable("unknown_vendor", f"Unknown API vendor '{vendor}'")
        encrypted = str(backend_config.get("encrypted_api_key") or "")
        if not encrypted:
            raise BackendUnavailable("missing_api_key", "No API key configured")
        api_key = ctx.cipher.decrypt_str(encrypted)
        model = backend_config.get("model")
        return cls(ctx.cfg, ctx.http, api_key, model)  # type: ignore[no-any-return]
    if btype == "cli":
        tool = str(backend_config.get("cli_tool") or "")
        if f"cli:{tool}" in _EXTRA_BACKENDS:
            return _EXTRA_BACKENDS[f"cli:{tool}"](ctx, backend_config)
        return CLIBackend(ctx.cfg, tool)
    raise BackendUnavailable("backend_not_configured", "No classification backend is configured")
