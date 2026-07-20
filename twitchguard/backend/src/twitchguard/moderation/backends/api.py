"""API-key backends: Anthropic, OpenAI, DeepSeek (FR-44, IR-24).

Shared httpx client with retry/backoff and 429 handling (FR-28, NFR-Rel-02).
Keys are decrypted in memory only and never logged (FR-45, AR-05).
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from ...config import ClassifierConfig
from .base import BackendUnavailable, CompletionResult, ModelBackend

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
}


class HttpApiBackend(ModelBackend):
    kind = "api"
    vendor = "abstract"

    def __init__(
        self, cfg: ClassifierConfig, http: httpx.AsyncClient, api_key: str, model: str | None
    ) -> None:
        super().__init__(cfg)
        self._http = http
        self._api_key = api_key
        self.model = model or DEFAULT_MODELS.get(self.vendor, "")

    async def _post_with_retries(
        self, url: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        delay = self.cfg.backoff_base_s
        last: str = "unreachable"
        for attempt in range(self.cfg.retry_attempts):
            try:
                resp = await self._http.post(
                    url, headers=headers, json=payload, timeout=self.cfg.api_timeout_s
                )
            except httpx.HTTPError as exc:
                last = f"network error: {type(exc).__name__}"
            else:
                if resp.status_code == 200:
                    return dict(resp.json())
                if resp.status_code in (401, 403):
                    raise BackendUnavailable("invalid_api_key", "The vendor rejected the API key")
                if resp.status_code == 429:
                    # FR-28: respect Retry-After, back off exponentially.
                    retry_after = float(resp.headers.get("retry-after") or delay)
                    last = "rate limited (429)"
                    if attempt == self.cfg.retry_attempts - 1:
                        raise BackendUnavailable(
                            "rate_limited", "Vendor rate limit hit", retry_after_s=retry_after
                        )
                    await asyncio.sleep(min(retry_after, self.cfg.backoff_max_s))
                    delay = min(delay * 2, self.cfg.backoff_max_s)
                    continue
                elif resp.status_code >= 500:
                    last = f"server error {resp.status_code}"
                else:
                    raise BackendUnavailable(
                        "vendor_error", f"Vendor returned HTTP {resp.status_code}"
                    )
            await asyncio.sleep(min(delay, self.cfg.backoff_max_s) + random.uniform(0, 0.2))
            delay = min(delay * 2, self.cfg.backoff_max_s)
        raise BackendUnavailable("backend_unreachable", f"Backend unavailable: {last}")

    async def validate(self) -> None:
        await self._ping()

    async def _ping(self) -> None:
        raise NotImplementedError


class AnthropicBackend(HttpApiBackend):
    vendor = "anthropic"
    base_url = "https://api.anthropic.com"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def complete(self, prompt: str) -> CompletionResult:
        data = await self._post_with_retries(
            f"{self.base_url}/v1/messages",
            self._headers(),
            {
                "model": self.model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        usage = data.get("usage") or {}
        tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        return CompletionResult(text=text, tokens=tokens)

    async def _ping(self) -> None:
        await self._post_with_retries(
            f"{self.base_url}/v1/messages",
            self._headers(),
            {
                "model": self.model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
        )


class OpenAIBackend(HttpApiBackend):
    vendor = "openai"
    base_url = "https://api.openai.com"
    chat_path = "/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "content-type": "application/json"}

    async def complete(self, prompt: str) -> CompletionResult:
        data = await self._post_with_retries(
            f"{self.base_url}{self.chat_path}",
            self._headers(),
            {
                "model": self.model,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        choices = data.get("choices") or [{}]
        text = str((choices[0].get("message") or {}).get("content") or "")
        tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
        return CompletionResult(text=text, tokens=tokens)

    async def _ping(self) -> None:
        await self._post_with_retries(
            f"{self.base_url}{self.chat_path}",
            self._headers(),
            {
                "model": self.model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
        )


class DeepSeekBackend(OpenAIBackend):
    """FR-48: DeepSeek exists only as an API backend (the vendor ships no CLI agent)."""

    vendor = "deepseek"
    base_url = "https://api.deepseek.com"
    chat_path = "/chat/completions"
