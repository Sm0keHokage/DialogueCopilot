"""ModelBackend interface (IR-23) with shared classify/retry logic (FR-25)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

from ...config import ClassifierConfig
from ...models import Rule
from ..prompts import CORRECTIVE_INSTRUCTION, build_prompt
from ..types import ChatMessage
from ..verdicts import Verdict, VerdictParseError, parse_verdicts


class BackendUnavailable(Exception):
    """Transient or configuration failure: the batch must NOT be dropped."""

    def __init__(self, code: str, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after_s = retry_after_s


class ClassificationError(Exception):
    """The model kept returning schema-invalid output after all retries (FR-27)."""

    def __init__(self, message: str, tokens: int = 0, requests: int = 0) -> None:
        super().__init__(message)
        self.tokens = tokens
        self.requests = requests


@dataclass
class CompletionResult:
    text: str
    tokens: int = 0


@dataclass
class ClassifyResult:
    verdicts: list[Verdict] = field(default_factory=list)
    tokens: int = 0
    requests: int = 0


class ModelBackend(ABC):
    """One classification source: a vendor API or a local CLI tool."""

    kind: str = "abstract"

    def __init__(self, cfg: ClassifierConfig) -> None:
        self.cfg = cfg

    @abstractmethod
    async def complete(self, prompt: str) -> CompletionResult:
        """Send one prompt, return raw text. Raise BackendUnavailable on transport failure."""

    @abstractmethod
    async def validate(self) -> None:
        """Cheap availability probe used by IR-14. Raise BackendUnavailable if broken."""

    async def classify(
        self, messages: Sequence[ChatMessage], rules: Sequence[Rule]
    ) -> ClassifyResult:
        """FR-25: parse strictly; on invalid JSON retry with a corrective instruction."""
        base_prompt = build_prompt(messages, rules)
        prompt = base_prompt
        tokens = 0
        requests = 0
        last_error = "unknown"
        for _attempt in range(self.cfg.max_retries + 1):
            result = await self.complete(prompt)
            tokens += result.tokens
            requests += 1
            try:
                verdicts = parse_verdicts(result.text)
            except VerdictParseError as exc:
                last_error = str(exc)
                prompt = f"{base_prompt}\n\n{CORRECTIVE_INSTRUCTION}\n(parser error: {exc})"
                continue
            return ClassifyResult(verdicts=verdicts, tokens=tokens, requests=requests)
        raise ClassificationError(
            f"model output stayed invalid after {self.cfg.max_retries + 1} attempts: {last_error}",
            tokens=tokens,
            requests=requests,
        )
